//+------------------------------------------------------------------+
//| SignalPublisher.mq5                                               |
//| Attach to ANY chart on the SOURCE account. Emits one JSON file    |
//| per trade event (OPEN/MODIFY/CLOSE) into MQL5/Files/edgeflow/out/ |
//| for the Python engine to pick up. See mql/README.md.              |
//| Uses OnTradeTransaction, so (unlike MQL4) no polling/diffing is   |
//| needed to detect position changes.                                |
//+------------------------------------------------------------------+
#property strict

input int ContextCandleCount = 30; // H1 bars sent with each OPEN, for SMC entry-context analysis
input string ScanSymbols = "";          // comma-separated watchlist for the live market scanner, e.g. "EURUSD,GBPUSD,XAUUSD" -- empty disables it
input int ScanIntervalSeconds = 300;    // how often to refresh each watchlist symbol's snapshot
input int ScanCandleCount = 60;         // H1 bars per snapshot -- wider than ContextCandleCount so older order blocks stay visible

string g_scanSymbols[];

int OnInit()
  {
   if(StringLen(ScanSymbols) > 0)
     {
      StringSplit(ScanSymbols, ',', g_scanSymbols);
      EventSetTimer(MathMax(ScanIntervalSeconds, 5));
     }
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

string SideOf(ENUM_POSITION_TYPE type)
  {
   return(type == POSITION_TYPE_BUY ? "BUY" : "SELL");
  }

// Snapshots the last ScanCandleCount H1 bars for one watchlist symbol, for
// the live market scanner (engine/market_scanner.py) -- independent of any
// open trade. Overwrites the same file each time; only the latest state
// matters, the Python side records what it finds elsewhere (market_patterns).
void WriteMarketSnapshot(string symbol)
  {
   string dir = "edgeflow\\market\\";
   string name = symbol + ".json";
   string tmpName = "." + name + ".tmp";

   int handle = FileOpen(dir + tmpName, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("edgeflow: failed to open %s err=%d", dir + tmpName, GetLastError());
      return;
     }

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   MqlRates rates[];
   int copied = CopyRates(symbol, PERIOD_H1, 1, ScanCandleCount, rates);

   FileWrite(handle, "{");
   FileWrite(handle, "  \"symbol\": \"", symbol, "\",");
   FileWrite(handle, "  \"candles\": [");
   for(int k = 0; k < copied; k++)
     {
      string comma = (k == copied - 1) ? "" : ",";
      FileWrite(handle, "    {\"time\": \"", TimeToString(rates[k].time, TIME_DATE | TIME_MINUTES),
                "\", \"open\": ", DoubleToString(rates[k].open, digits),
                ", \"high\": ", DoubleToString(rates[k].high, digits),
                ", \"low\": ", DoubleToString(rates[k].low, digits),
                ", \"close\": ", DoubleToString(rates[k].close, digits), "}", comma);
     }
   FileWrite(handle, "  ]");
   FileWrite(handle, "}");
   FileClose(handle);

   if(!FileMove(dir + tmpName, 0, dir + name, FILE_REWRITE))
      PrintFormat("edgeflow: failed to finalize market snapshot %s err=%d", name, GetLastError());
  }

void OnTimer()
  {
   for(int i = 0; i < ArraySize(g_scanSymbols); i++)
      WriteMarketSnapshot(g_scanSymbols[i]);
  }

// Writes the last ContextCandleCount closed H1 bars as a JSON array, for
// SMC entry-context analysis on the Python side (engine/smc_analysis.py).
void WriteContextCandles(int handle, string symbol, int digits)
  {
   MqlRates rates[];
   int copied = CopyRates(symbol, PERIOD_H1, 1, ContextCandleCount, rates);
   FileWrite(handle, "  \"context_candles\": [");
   for(int k = 0; k < copied; k++)
     {
      string comma = (k == copied - 1) ? "" : ",";
      FileWrite(handle, "    {\"time\": \"", TimeToString(rates[k].time, TIME_DATE | TIME_MINUTES),
                "\", \"open\": ", DoubleToString(rates[k].open, digits),
                ", \"high\": ", DoubleToString(rates[k].high, digits),
                ", \"low\": ", DoubleToString(rates[k].low, digits),
                ", \"close\": ", DoubleToString(rates[k].close, digits), "}", comma);
     }
   FileWrite(handle, "  ],");
  }

void EmitEvent(string eventName, ulong ticket, string symbol, ENUM_POSITION_TYPE type,
               double volume, double entry, double sl, double tp, bool includeContext)
  {
   string dir = "edgeflow\\out\\";
   string name = IntegerToString((long)ticket) + "_" + eventName + "_" +
                 IntegerToString(MathRand()) + ".json";
   string tmpName = "." + name + ".tmp";

   int handle = FileOpen(dir + tmpName, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("edgeflow: failed to open %s err=%d", dir + tmpName, GetLastError());
      return;
     }

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   FileWrite(handle, "{");
   FileWrite(handle, "  \"ticket\": ", (long)ticket, ",");
   FileWrite(handle, "  \"event\": \"", eventName, "\",");
   FileWrite(handle, "  \"symbol\": \"", symbol, "\",");
   FileWrite(handle, "  \"side\": \"", SideOf(type), "\",");
   FileWrite(handle, "  \"volume\": ", DoubleToString(volume, 2), ",");
   FileWrite(handle, "  \"entry_price\": ", DoubleToString(entry, digits), ",");
   FileWrite(handle, "  \"stop_loss\": ", DoubleToString(sl, digits), ",");
   FileWrite(handle, "  \"take_profit\": ", DoubleToString(tp, digits), ",");
   FileWrite(handle, "  \"equity\": ", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2), ",");
   if(includeContext)
      WriteContextCandles(handle, symbol, digits);
   FileWrite(handle, "  \"timestamp\": ", TimeCurrent());
   FileWrite(handle, "}");
   FileClose(handle);

   if(!FileMove(dir + tmpName, 0, dir + name, FILE_REWRITE))
      PrintFormat("edgeflow: failed to finalize %s err=%d", name, GetLastError());
  }

// CLOSE/PARTIAL_CLOSE need the actual close price and realized profit --
// without this, nothing downstream can ever learn which trades (and entry
// patterns) were actually good ones. `volume` means different things per
// eventName: the closed amount for CLOSE, the amount STILL REMAINING for
// PARTIAL_CLOSE (see models.py's TradeSignal docstring on the Python side).
void EmitCloseEvent(string eventName, ulong ticket, string symbol, ENUM_POSITION_TYPE type,
                     double volume, double openPrice, double closePrice, double profit,
                     double sl, double tp)
  {
   string dir = "edgeflow\\out\\";
   string name = IntegerToString((long)ticket) + "_" + eventName + "_" + IntegerToString(MathRand()) + ".json";
   string tmpName = "." + name + ".tmp";

   int handle = FileOpen(dir + tmpName, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("edgeflow: failed to open %s err=%d", dir + tmpName, GetLastError());
      return;
     }

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   FileWrite(handle, "{");
   FileWrite(handle, "  \"ticket\": ", (long)ticket, ",");
   FileWrite(handle, "  \"event\": \"", eventName, "\",");
   FileWrite(handle, "  \"symbol\": \"", symbol, "\",");
   FileWrite(handle, "  \"side\": \"", SideOf(type), "\",");
   FileWrite(handle, "  \"volume\": ", DoubleToString(volume, 2), ",");
   FileWrite(handle, "  \"entry_price\": ", DoubleToString(openPrice, digits), ",");
   FileWrite(handle, "  \"close_price\": ", DoubleToString(closePrice, digits), ",");
   FileWrite(handle, "  \"profit\": ", DoubleToString(profit, 2), ",");
   FileWrite(handle, "  \"stop_loss\": ", DoubleToString(sl, digits), ",");
   FileWrite(handle, "  \"take_profit\": ", DoubleToString(tp, digits), ",");
   FileWrite(handle, "  \"equity\": ", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2), ",");
   FileWrite(handle, "  \"timestamp\": ", TimeCurrent());
   FileWrite(handle, "}");
   FileClose(handle);

   if(!FileMove(dir + tmpName, 0, dir + name, FILE_REWRITE))
      PrintFormat("edgeflow: failed to finalize %s err=%d", name, GetLastError());
  }

void OnTradeTransaction(const MqlTradeTransaction &trans,
                         const MqlTradeRequest &request,
                         const MqlTradeResult &result)
  {
   // SL/TP edits (no volume change) arrive as TRADE_TRANSACTION_POSITION,
   // not as a deal -- handle that case separately from opens/closes below.
   if(trans.type == TRADE_TRANSACTION_POSITION)
     {
      if(PositionSelectByTicket(trans.position))
        {
         ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         EmitEvent("MODIFY", trans.position, PositionGetString(POSITION_SYMBOL), type,
                   PositionGetDouble(POSITION_VOLUME), PositionGetDouble(POSITION_PRICE_OPEN),
                   PositionGetDouble(POSITION_SL), PositionGetDouble(POSITION_TP), false);
        }
      return;
     }

   if(trans.type != TRADE_TRANSACTION_DEAL_ADD)
      return;

   if(!HistoryDealSelect(trans.deal))
      return;

   ulong positionId = HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   string symbol = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
   double dealVolume = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
   double dealPrice = HistoryDealGetDouble(trans.deal, DEAL_PRICE);

   if(entry == DEAL_ENTRY_IN)
     {
      if(PositionSelectByTicket(positionId))
        {
         ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         EmitEvent("OPEN", positionId, symbol, type, dealVolume, dealPrice,
                   PositionGetDouble(POSITION_SL), PositionGetDouble(POSITION_TP), true);
        }
     }
   else if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
     {
      ENUM_POSITION_TYPE type = (HistoryDealGetInteger(trans.deal, DEAL_TYPE) == DEAL_TYPE_SELL)
                                 ? POSITION_TYPE_BUY : POSITION_TYPE_SELL; // closing deal is opposite side

      // Recover the position's original open price from its first deal --
      // PositionSelectByTicket() no longer works once the position is closed.
      double openPrice = dealPrice;
      if(HistorySelectByPosition(positionId))
        {
         for(int i = 0; i < HistoryDealsTotal(); i++)
           {
            ulong dealTicket = HistoryDealGetTicket(i);
            if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(dealTicket, DEAL_ENTRY) == DEAL_ENTRY_IN)
              {
               openPrice = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
               break;
              }
           }
        }

      double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT) +
                       HistoryDealGetDouble(trans.deal, DEAL_SWAP) +
                       HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);

      // If the position still exists after this OUT deal, only part of it
      // was closed -- report the volume STILL REMAINING, not what closed.
      if(PositionSelectByTicket(positionId))
        {
         double remainingVolume = PositionGetDouble(POSITION_VOLUME);
         EmitCloseEvent("PARTIAL_CLOSE", positionId, symbol, type, remainingVolume,
                        openPrice, dealPrice, profit,
                        PositionGetDouble(POSITION_SL), PositionGetDouble(POSITION_TP));
        }
      else
        {
         EmitCloseEvent("CLOSE", positionId, symbol, type, dealVolume, openPrice, dealPrice, profit, 0, 0);
        }
     }
  }
//+------------------------------------------------------------------+
