"""Scalp-specific private history reads; the existing Swing transport is unchanged."""
from dataclasses import replace
from datetime import datetime, timezone
from customer.context import TenantYellowTransport
from execution.transport import VenueUnavailable
from execution.yellow_transport import normalise_trade, normalise_order


class ScalpYellowTransport(TenantYellowTransport):
    def place_order(self, request):
        client = self.c.ensure()
        if not request.reduce_only and not client.entries_allowed():
            raise VenueUnavailable(0, 'global_entries_disabled')
        return super().place_order(request)

    def get_orders(self, account_id, market=None, since=None, cursor=None):
        # Scalp creates many short-lived quotes. The shared adapter's one-order
        # pages exhaust the bounded history walk before older active exits can
        # be verified. Use the venue's supported 100-record cursor pages.
        client=self.c.ensure()
        data=self._wrap(client._page,'/perpetual/orders',
                        {'app_session_id':client.asid,'market':market},cursor,100)
        if data.get('degraded') or data.get('stale'):
            raise VenueUnavailable(503,'scalp_order_history_stale')
        if data.get('has_more') and not data.get('next_cursor'):
            raise VenueUnavailable(503,'scalp_order_history_missing_cursor')
        return ([normalise_order(row) for row in data.get('orders',[])],
                (data.get('next_cursor') or None) if data.get('has_more') else None)

    def get_trades(self, account_id, market=None, since=None, cursor=None):
        start = datetime.fromtimestamp(since, timezone.utc).isoformat() if since is not None else None
        data = self._wrap(self.c.trades_page, market, cursor, start)
        if data.get('degraded') or data.get('stale'):
            raise VenueUnavailable(503, 'scalp_trade_history_stale')
        result = []
        for row in data.get('trades', []):
            trade = normalise_trade(row, account_id)
            if not trade.ts:
                raw = row.get('executed_at') or row.get('created_at')
                if raw:
                    trade = replace(trade, ts=datetime.fromisoformat(str(raw).replace('Z', '+00:00')).timestamp())
            result.append(trade)
        if data.get('has_more') and not data.get('next_cursor'):
            raise VenueUnavailable(503, 'scalp_trade_history_missing_cursor')
        return result, (data.get('next_cursor') or None) if data.get('has_more') else None
