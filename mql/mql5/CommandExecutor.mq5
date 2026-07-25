//+------------------------------------------------------------------+
//| CommandExecutor.mq5                                               |
//| Attach to ANY chart on a TARGET account. Polls                    |
//| MQL5/Files/edgeflow/in/ for CopyCommand JSON files written by the |
//| Python engine, executes the matching order, and publishes an     |
//| equity heartbeat every tick. See mql/README.md.                   |
//+------------------------------------------------------------------+
#property strict
#include <Trade\Trade.mqh>

input int PollMillis = 500;
input ulong MagicNumber = 424242;

CTrade trade;

// Maps a source ticket to the position ticket opened locally for it.
ulong g_sourceTickets[];
ulong g_localTickets[];

ulong FindLocalTicket(ulong sourceTicket)
  {
   for(int i = 0; i < ArraySize(g_sourceTickets); i++)
      if(g_sourceTickets[i] == sourceTicket)
         return g_localTickets[i];
   return 0;
  }

void RememberMapping(ulong sourceTicket, ulong localTicket)
  {
   int n = ArraySize(g_sourceTickets);
   ArrayResize(g_sourceTickets, n + 1);
   ArrayResize(g_localTickets, n + 1);
   g_sourceTickets[n] = sourceTicket;
   g_localTickets[n] = localTicket;
  }

// Positions opened by HandleCommandFile() below carry "edgeflow:<sourceTicket>"
// as their comment -- this recovers that source ticket from it.
ulong ParseSourceTicketFromComment(string comment)
  {
   string prefix = "edgeflow:";
   if(StringFind(comment, prefix) != 0)
      return 0;
   return (ulong)StringToInteger(StringSubstr(comment, StringLen(prefix)));
  }

int OnInit()
  {
   trade.SetExpertMagicNumber(MagicNumber);
   EventSetMillisecondTimer(PollMillis);
   ArrayResize(g_sourceTickets, 0);
   ArrayResize(g_localTickets, 0);

   // Recover the source-ticket -> local-ticket mapping after a restart
   // (VPS reboot, terminal update, EA reload). Without this, a position
   // already open here becomes orphaned: no MODIFY or CLOSE from the
   // source could ever be matched to it again, leaving it open forever
   // even after the source closes.
   for(int i = 0; i < PositionsTotal(); i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)MagicNumber)
         continue;
      ulong sourceTicket = ParseSourceTicketFromComment(PositionGetString(POSITION_COMMENT));
      if(sourceTicket != 0)
         RememberMapping(sourceTicket, ticket);
     }

   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

string JsonString(string body, string key)
  {
   string needle = "\"" + key + "\"";
   int pos = StringFind(body, needle);
   if(pos < 0)
      return("");
   int colon = StringFind(body, ":", pos);
   int start = StringFind(body, "\"", colon + 1) + 1;
   int end = StringFind(body, "\"", start);
   return(StringSubstr(body, start, end - start));
  }

double JsonNumber(string body, string key)
  {
   string needle = "\"" + key + "\"";
   int pos = StringFind(body, needle);
   if(pos < 0)
      return(0);
   int colon = StringFind(body, ":", pos);
   int start = colon + 1;
   int end = StringFind(body, ",", start);
   int endBrace = StringFind(body, "}", start);
   if(end < 0 || (endBrace >= 0 && endBrace < end))
      end = endBrace;
   string s = StringSubstr(body, start, end - start);
   StringTrimLeft(s);
   StringTrimRight(s);
   return(StringToDouble(s));
  }

string ReadWholeFile(string path)
  {
   int handle = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return("");
   string content = "";
   while(!FileIsEnding(handle))
      content += FileReadString(handle) + "\n";
   FileClose(handle);
   return(content);
  }

void HandleCommandFile(string filename)
  {
   string dir = "edgeflow\\in\\";
   string body = ReadWholeFile(dir + filename);
   if(StringLen(body) == 0)
      return;

   string event = JsonString(body, "event");
   string symbol = JsonString(body, "symbol");
   string side = JsonString(body, "side");
   double volume = JsonNumber(body, "volume");
   double sl = JsonNumber(body, "stop_loss");
   double tp = JsonNumber(body, "take_profit");
   ulong sourceTicket = (ulong)JsonNumber(body, "source_ticket");

   if(event == "OPEN")
     {
      string comment = "edgeflow:" + IntegerToString((long)sourceTicket);
      bool ok = (side == "BUY") ? trade.Buy(volume, symbol, 0, sl, tp, comment)
                                 : trade.Sell(volume, symbol, 0, sl, tp, comment);
      if(!ok)
         PrintFormat("edgeflow: open failed for %s err=%d", symbol, GetLastError());
      else
         RememberMapping(sourceTicket, trade.ResultOrder());
     }
   else if(event == "MODIFY")
     {
      ulong localTicket = FindLocalTicket(sourceTicket);
      if(localTicket != 0 && PositionSelectByTicket(localTicket))
         if(!trade.PositionModify(localTicket, sl, tp))
            PrintFormat("edgeflow: modify failed for %I64u err=%d", localTicket, GetLastError());
     }
   else if(event == "CLOSE")
     {
      ulong localTicket = FindLocalTicket(sourceTicket);
      if(localTicket != 0 && PositionSelectByTicket(localTicket))
         if(!trade.PositionClose(localTicket))
            PrintFormat("edgeflow: close failed for %I64u err=%d", localTicket, GetLastError());
     }

   string doneDir = "edgeflow\\in\\done\\";
   if(!FileMove(dir + filename, 0, doneDir + filename, FILE_REWRITE))
      PrintFormat("edgeflow: failed to archive %s err=%d", filename, GetLastError());
  }

void PublishHeartbeat()
  {
   int handle = FileOpen("edgeflow\\.heartbeat.json.tmp", FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return;
   FileWrite(handle, "{");
   FileWrite(handle, "  \"equity\": ", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2), ",");
   FileWrite(handle, "  \"balance\": ", DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2), ",");
   FileWrite(handle, "  \"timestamp\": ", TimeCurrent());
   FileWrite(handle, "}");
   FileClose(handle);
   FileMove("edgeflow\\.heartbeat.json.tmp", 0, "edgeflow\\heartbeat.json", FILE_REWRITE);
  }

void OnTimer()
  {
   PublishHeartbeat();

   string filename;
   long searchHandle = FileFindFirst("edgeflow\\in\\*.json", filename);
   if(searchHandle == INVALID_HANDLE)
      return;
   do
     {
      HandleCommandFile(filename);
     }
   while(FileFindNext(searchHandle, filename));
   FileFindClose(searchHandle);
  }
//+------------------------------------------------------------------+
