//+------------------------------------------------------------------+
//| SignalPublisher.mq4                                              |
//| Attach to ANY chart on the SOURCE account. Emits one JSON file    |
//| per trade event (OPEN/MODIFY/CLOSE) into MQL4/Files/edgeflow/out/ |
//| for the Python engine to pick up. See mql/README.md.              |
//+------------------------------------------------------------------+
#property strict

input int PollMillis = 500;
input int ContextCandleCount = 30; // H1 bars sent with each OPEN, for SMC entry-context analysis

// Parallel arrays tracking the last known state of every open ticket,
// since MT4 has no OnTradeTransaction event -- we diff snapshots instead.
int    g_tickets[];
double g_volumes[];
double g_stopLoss[];
double g_takeProfit[];

int OnInit()
  {
   EventSetMillisecondTimer(PollMillis);
   ArrayResize(g_tickets, 0);
   ArrayResize(g_volumes, 0);
   ArrayResize(g_stopLoss, 0);
   ArrayResize(g_takeProfit, 0);

   // Recover state after a restart (VPS reboot, terminal update, EA
   // reload): adopt currently open positions as already-tracked instead of
   // treating them as new on the first OnTimer() tick. Without this, every
   // restart would re-emit an OPEN for each already-open position, which
   // the target account would duplicate on top of what it already holds.
   for(int i = 0; i < OrdersTotal(); i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;
      AddTracked(OrderTicket(), OrderLots(), OrderStopLoss(), OrderTakeProfit());
     }

   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

int FindTracked(int ticket)
  {
   for(int i = 0; i < ArraySize(g_tickets); i++)
      if(g_tickets[i] == ticket)
         return i;
   return -1;
  }

void RemoveTracked(int idx)
  {
   int last = ArraySize(g_tickets) - 1;
   g_tickets[idx]    = g_tickets[last];
   g_volumes[idx]    = g_volumes[last];
   g_stopLoss[idx]   = g_stopLoss[last];
   g_takeProfit[idx] = g_takeProfit[last];
   ArrayResize(g_tickets, last);
   ArrayResize(g_volumes, last);
   ArrayResize(g_stopLoss, last);
   ArrayResize(g_takeProfit, last);
  }

void AddTracked(int ticket, double volume, double sl, double tp)
  {
   int n = ArraySize(g_tickets);
   ArrayResize(g_tickets, n + 1);
   ArrayResize(g_volumes, n + 1);
   ArrayResize(g_stopLoss, n + 1);
   ArrayResize(g_takeProfit, n + 1);
   g_tickets[n] = ticket;
   g_volumes[n] = volume;
   g_stopLoss[n] = sl;
   g_takeProfit[n] = tp;
  }

string SideOf(int type)
  {
   return(type == OP_BUY ? "BUY" : "SELL");
  }

string NowIso()
  {
   return(TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS));
  }

// Writes the last ContextCandleCount closed H1 bars as a JSON array, for
// SMC entry-context analysis on the Python side (engine/smc_analysis.py).
// Only called for OPEN events -- context at entry is what matters.
void WriteContextCandles(int handle, string symbol)
  {
   FileWrite(handle, "  \"context_candles\": [");
   for(int k = ContextCandleCount; k >= 1; k--)
     {
      string comma = (k == 1) ? "" : ",";
      FileWrite(handle, "    {\"time\": \"", TimeToString(iTime(symbol, PERIOD_H1, k), TIME_DATE | TIME_MINUTES),
                "\", \"open\": ", DoubleToString(iOpen(symbol, PERIOD_H1, k), Digits),
                ", \"high\": ", DoubleToString(iHigh(symbol, PERIOD_H1, k), Digits),
                ", \"low\": ", DoubleToString(iLow(symbol, PERIOD_H1, k), Digits),
                ", \"close\": ", DoubleToString(iClose(symbol, PERIOD_H1, k), Digits), "}", comma);
     }
   FileWrite(handle, "  ],");
  }

// Writes <ticket>_<event>_<rand>.json atomically (write to .tmp, then rename).
void EmitEvent(string eventName, int ticket, string symbol, int type,
               double volume, double entry, double sl, double tp, bool includeContext)
  {
   string dir = "edgeflow\\out\\";
   string name = IntegerToString(ticket) + "_" + eventName + "_" +
                 IntegerToString(MathRand()) + ".json";
   string tmpName = "." + name + ".tmp";

   int handle = FileOpen(dir + tmpName, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      Print("edgeflow: failed to open ", dir + tmpName, " err=", GetLastError());
      return;
     }

   FileWrite(handle, "{");
   FileWrite(handle, "  \"ticket\": ", ticket, ",");
   FileWrite(handle, "  \"event\": \"", eventName, "\",");
   FileWrite(handle, "  \"symbol\": \"", symbol, "\",");
   FileWrite(handle, "  \"side\": \"", SideOf(type), "\",");
   FileWrite(handle, "  \"volume\": ", DoubleToString(volume, 2), ",");
   FileWrite(handle, "  \"entry_price\": ", DoubleToString(entry, Digits), ",");
   FileWrite(handle, "  \"stop_loss\": ", DoubleToString(sl, Digits), ",");
   FileWrite(handle, "  \"take_profit\": ", DoubleToString(tp, Digits), ",");
   FileWrite(handle, "  \"equity\": ", DoubleToString(AccountEquity(), 2), ",");
   if(includeContext)
      WriteContextCandles(handle, symbol);
   FileWrite(handle, "  \"timestamp\": \"", NowIso(), "\"");
   FileWrite(handle, "}");
   FileClose(handle);

   if(!FileMove(dir + tmpName, 0, dir + name, FILE_REWRITE))
      Print("edgeflow: failed to finalize ", name, " err=", GetLastError());
  }

// CLOSE needs the actual close price and realized profit, not the OPEN
// price EmitEvent()'s "entry" parameter carries -- without this, nothing
// downstream can ever learn which trades (and entry patterns) were
// actually good ones. Requires OrderSelect() to already be pointing at
// the closed order's history record (see the caller in OnTimer()).
void EmitCloseEvent(int ticket, string symbol, int type, double volume,
                     double openPrice, double sl, double tp)
  {
   string dir = "edgeflow\\out\\";
   string name = IntegerToString(ticket) + "_CLOSE_" + IntegerToString(MathRand()) + ".json";
   string tmpName = "." + name + ".tmp";

   int handle = FileOpen(dir + tmpName, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      Print("edgeflow: failed to open ", dir + tmpName, " err=", GetLastError());
      return;
     }

   FileWrite(handle, "{");
   FileWrite(handle, "  \"ticket\": ", ticket, ",");
   FileWrite(handle, "  \"event\": \"CLOSE\",");
   FileWrite(handle, "  \"symbol\": \"", symbol, "\",");
   FileWrite(handle, "  \"side\": \"", SideOf(type), "\",");
   FileWrite(handle, "  \"volume\": ", DoubleToString(volume, 2), ",");
   FileWrite(handle, "  \"entry_price\": ", DoubleToString(openPrice, Digits), ",");
   FileWrite(handle, "  \"close_price\": ", DoubleToString(OrderClosePrice(), Digits), ",");
   FileWrite(handle, "  \"profit\": ", DoubleToString(OrderProfit() + OrderSwap() + OrderCommission(), 2), ",");
   FileWrite(handle, "  \"stop_loss\": ", DoubleToString(sl, Digits), ",");
   FileWrite(handle, "  \"take_profit\": ", DoubleToString(tp, Digits), ",");
   FileWrite(handle, "  \"equity\": ", DoubleToString(AccountEquity(), 2), ",");
   FileWrite(handle, "  \"timestamp\": \"", NowIso(), "\"");
   FileWrite(handle, "}");
   FileClose(handle);

   if(!FileMove(dir + tmpName, 0, dir + name, FILE_REWRITE))
      Print("edgeflow: failed to finalize ", name, " err=", GetLastError());
  }

void OnTimer()
  {
   bool seen[];
   ArrayResize(seen, ArraySize(g_tickets));
   ArrayInitialize(seen, false);

   for(int i = 0; i < OrdersTotal(); i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;
      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue; // ignore pending orders in this MVP

      int idx = FindTracked(OrderTicket());
      if(idx == -1)
        {
         AddTracked(OrderTicket(), OrderLots(), OrderStopLoss(), OrderTakeProfit());
         EmitEvent("OPEN", OrderTicket(), OrderSymbol(), OrderType(),
                   OrderLots(), OrderOpenPrice(), OrderStopLoss(), OrderTakeProfit(), true);
        }
      else
        {
         seen[idx] = true;
         bool changed = (g_volumes[idx] != OrderLots() ||
                         g_stopLoss[idx] != OrderStopLoss() ||
                         g_takeProfit[idx] != OrderTakeProfit());
         if(changed)
           {
            g_volumes[idx] = OrderLots();
            g_stopLoss[idx] = OrderStopLoss();
            g_takeProfit[idx] = OrderTakeProfit();
            EmitEvent("MODIFY", OrderTicket(), OrderSymbol(), OrderType(),
                      OrderLots(), OrderOpenPrice(), OrderStopLoss(), OrderTakeProfit(), false);
           }
        }
     }

   // Anything tracked but not seen this pass was closed on the source.
   for(int i = ArraySize(g_tickets) - 1; i >= 0; i--)
     {
      if(i < ArraySize(seen) && seen[i])
         continue;
      if(OrderSelect(g_tickets[i], SELECT_BY_TICKET, MODE_HISTORY))
         EmitCloseEvent(g_tickets[i], OrderSymbol(), OrderType(),
                         g_volumes[i], OrderOpenPrice(), g_stopLoss[i], g_takeProfit[i]);
      RemoveTracked(i);
     }
  }
//+------------------------------------------------------------------+
