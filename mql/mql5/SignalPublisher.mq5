//+------------------------------------------------------------------+
//| SignalPublisher.mq5                                               |
//| Attach to ANY chart on the SOURCE account. Emits one JSON file    |
//| per trade event (OPEN/MODIFY/CLOSE) into MQL5/Files/edgeflow/out/ |
//| for the Python engine to pick up. See mql/README.md.              |
//| Uses OnTradeTransaction, so (unlike MQL4) no polling/diffing is   |
//| needed to detect position changes.                                |
//+------------------------------------------------------------------+
#property strict

string SideOf(ENUM_POSITION_TYPE type)
  {
   return(type == POSITION_TYPE_BUY ? "BUY" : "SELL");
  }

void EmitEvent(string eventName, ulong ticket, string symbol, ENUM_POSITION_TYPE type,
               double volume, double entry, double sl, double tp)
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
                   PositionGetDouble(POSITION_SL), PositionGetDouble(POSITION_TP));
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
                   PositionGetDouble(POSITION_SL), PositionGetDouble(POSITION_TP));
        }
     }
   else if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
     {
      ENUM_POSITION_TYPE type = (HistoryDealGetInteger(trans.deal, DEAL_TYPE) == DEAL_TYPE_SELL)
                                 ? POSITION_TYPE_BUY : POSITION_TYPE_SELL; // closing deal is opposite side
      EmitEvent("CLOSE", positionId, symbol, type, dealVolume, dealPrice, 0, 0);
     }
  }
//+------------------------------------------------------------------+
