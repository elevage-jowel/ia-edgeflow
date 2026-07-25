//+------------------------------------------------------------------+
//| CommandExecutor.mq4                                               |
//| Attach to ANY chart on a TARGET account. Polls                    |
//| MQL4/Files/edgeflow/in/ for CopyCommand JSON files written by the |
//| Python engine, executes the matching order, and publishes an     |
//| equity heartbeat every tick. See mql/README.md.                   |
//+------------------------------------------------------------------+
#property strict

input int    PollMillis = 500;
input int    Slippage = 5;
input int    MagicNumber = 424242;

// Maps a source ticket to the ticket opened locally for it, so CLOSE/MODIFY
// commands referencing the source ticket know which local order to act on.
int g_sourceTickets[];
int g_localTickets[];

int FindLocalTicket(int sourceTicket)
  {
   for(int i = 0; i < ArraySize(g_sourceTickets); i++)
      if(g_sourceTickets[i] == sourceTicket)
         return g_localTickets[i];
   return -1;
  }

void RememberMapping(int sourceTicket, int localTicket)
  {
   int n = ArraySize(g_sourceTickets);
   ArrayResize(g_sourceTickets, n + 1);
   ArrayResize(g_localTickets, n + 1);
   g_sourceTickets[n] = sourceTicket;
   g_localTickets[n] = localTicket;
  }

// Orders opened by HandleCommandFile() below carry "edgeflow:<sourceTicket>"
// as their comment -- this recovers that source ticket from it.
int ParseSourceTicketFromComment(string comment)
  {
   string prefix = "edgeflow:";
   if(StringFind(comment, prefix) != 0)
      return -1;
   return (int)StringToInteger(StringSubstr(comment, StringLen(prefix)));
  }

int OnInit()
  {
   EventSetMillisecondTimer(PollMillis);
   ArrayResize(g_sourceTickets, 0);
   ArrayResize(g_localTickets, 0);

   // Recover the source-ticket -> local-ticket mapping after a restart
   // (VPS reboot, terminal update, EA reload). Without this, a position
   // already open here becomes orphaned: no MODIFY or CLOSE from the
   // source could ever be matched to it again, leaving it open forever
   // even after the source closes.
   for(int i = 0; i < OrdersTotal(); i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderMagicNumber() != MagicNumber)
         continue;
      int sourceTicket = ParseSourceTicketFromComment(OrderComment());
      if(sourceTicket > 0)
         RememberMapping(sourceTicket, OrderTicket());
     }

   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

// Extremely small JSON reader for our own fixed schema -- not a general
// parser. Good enough because we control both the writer and the reader.
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
   int sourceTicket = (int)JsonNumber(body, "source_ticket");

   int type = (side == "BUY" ? OP_BUY : OP_SELL);

   if(event == "OPEN")
     {
      double price = (type == OP_BUY) ? MarketInfo(symbol, MODE_ASK) : MarketInfo(symbol, MODE_BID);
      int ticket = OrderSend(symbol, type, volume, price, Slippage, sl, tp,
                              "edgeflow:" + IntegerToString(sourceTicket), MagicNumber, 0, clrNONE);
      if(ticket < 0)
         Print("edgeflow: OrderSend failed for ", symbol, " err=", GetLastError());
      else
         RememberMapping(sourceTicket, ticket);
     }
   else if(event == "MODIFY")
     {
      int localTicket = FindLocalTicket(sourceTicket);
      if(localTicket > 0 && OrderSelect(localTicket, SELECT_BY_TICKET))
         if(!OrderModify(localTicket, OrderOpenPrice(), sl, tp, 0, clrNONE))
            Print("edgeflow: OrderModify failed for ", localTicket, " err=", GetLastError());
     }
   else if(event == "CLOSE")
     {
      int localTicket = FindLocalTicket(sourceTicket);
      if(localTicket > 0 && OrderSelect(localTicket, SELECT_BY_TICKET))
        {
         double price = (OrderType() == OP_BUY) ? MarketInfo(OrderSymbol(), MODE_BID)
                                                  : MarketInfo(OrderSymbol(), MODE_ASK);
         if(!OrderClose(localTicket, OrderLots(), price, Slippage, clrNONE))
            Print("edgeflow: OrderClose failed for ", localTicket, " err=", GetLastError());
        }
     }
   else if(event == "PARTIAL_CLOSE")
     {
      // "volume" here is the DESIRED REMAINING size (see models.py's
      // CopyCommand docstring) -- close just enough to reach it.
      int localTicket = FindLocalTicket(sourceTicket);
      if(localTicket > 0 && OrderSelect(localTicket, SELECT_BY_TICKET))
        {
         double toClose = OrderLots() - volume;
         if(toClose > 0)
           {
            double price = (OrderType() == OP_BUY) ? MarketInfo(OrderSymbol(), MODE_BID)
                                                     : MarketInfo(OrderSymbol(), MODE_ASK);
            if(!OrderClose(localTicket, NormalizeDouble(toClose, 2), price, Slippage, clrNONE))
               Print("edgeflow: partial OrderClose failed for ", localTicket, " err=", GetLastError());
           }
        }
     }

   string doneDir = "edgeflow\\in\\done\\";
   if(!FileMove(dir + filename, 0, doneDir + filename, FILE_REWRITE))
      Print("edgeflow: failed to archive ", filename, " err=", GetLastError());
  }

void PublishHeartbeat()
  {
   int handle = FileOpen("edgeflow\\.heartbeat.json.tmp", FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return;
   FileWrite(handle, "{");
   FileWrite(handle, "  \"equity\": ", DoubleToString(AccountEquity(), 2), ",");
   FileWrite(handle, "  \"balance\": ", DoubleToString(AccountBalance(), 2), ",");
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
