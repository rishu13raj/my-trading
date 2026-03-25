"""
strategy/pipeline.py — The Orchestrator

PURPOSE
───────
TradePipeline wires the four phases (Scanner → Incubator → Entry → Monitor)
into a single coherent flow.  It is the only component that knows about all
phases and the order they run.  Each phase only knows about its own job.

main.py creates ONE TradePipeline instance at startup and calls on_tick() for
every tick received from Zerodha's WebSocket.  That's it — main.py's job is
infrastructure (FastAPI, WebSocket, EOD square-off); the pipeline's job is
trade logic.

TICK FLOW
──────────
Every tick goes through this exact sequence:

  WebSocket → main.py.on_tick_received() → pipeline.on_tick()

                                      ┌──────────────────────────────────────┐
  Zerodha tick ──→ pipeline.on_tick() │                                      │
                                      │  [Phase 4: Monitor]  always first    │
                                      │    ↓ ExitDecision?                   │
                                      │      yes → _execute_exit()           │
                                      │            record_exit()             │
                                      │            RETURN (no entry this     │
                                      │            tick)                     │
                                      │                                      │
                                      │  Gate 1: symbol on watchlist?        │
                                      │  Gate 2: symbol paused?              │
                                      │  Gate 3: already holding symbol?     │
                                      │  Gate 4: portfolio at capacity?      │
                                      │    any gate fails → RETURN           │
                                      │                                      │
                                      │  [Phase 1: Scanner]                  │
                                      │    → CandidateSignal or None         │
                                      │                                      │
                                      │  [Phase 2: Incubator]                │
                                      │    → ConfirmedSignal or None         │
                                      │    (incubation state persists        │
                                      │     across ticks — see incubator.py) │
                                      │                                      │
                                      │  [Phase 3: Entry]                    │
                                      │    → OpenPosition or None            │
                                      └──────────────────────────────────────┘

WHY MONITOR RUNS FIRST
──────────────────────
Consider: ADANIPOWER is in an active SELL position at ₹154.  A tick arrives
showing LTP=157.00 (which would hit the stop loss).  Should we exit or check
the entry signal first?

We MUST exit first.  If we ran the scanner first and it fired another signal,
we'd try to open a second ADANIPOWER position while the first was still open
(Gate 3 would block it — but it's cleaner and faster to just exit first).

More importantly: the exit check must ALWAYS run, even if the symbol is
paused for new entries.  "Paused" means "don't open new trades", not "ignore
existing positions".  The monitor loop intentionally runs before the watchlist
and paused gates.

WHY NO ENTRY ON THE SAME TICK AS AN EXIT
──────────────────────────────────────────
After an exit we `return` immediately.  This is deliberate.

Scenario: MOMENTUM_FLIP — we just closed a BUY position and immediately opened
a SELL.  The reverse entry is handled inside _execute_exit(), not by running
the full scanner → incubator → entry pipeline again.  If we didn't return, we'd
run the scanner on the same tick that just saw a flip, potentially generating
a duplicate or conflicting signal.

For non-flip exits: after a STOP_LOSS or PROFIT_TRAIL, the market state that
caused the exit is still active.  The scanner might fire again on the same tick
that just triggered the stop.  Running entry immediately after an exit on the
same tick would bypass the cooldown and incubator — exactly what we're trying
to prevent.

MOMENTUM_FLIP — THE SPECIAL EXIT
──────────────────────────────────
A MOMENTUM_FLIP is when the position's own momentum reverses strongly enough
to warrant immediately going the other way.  It's handled differently from
all other exits:

Normal exits (STOP_LOSS, PROFIT_TRAIL, etc.):
  close position → record_exit() → return → next tick, cooldown + incubation apply

Flip exit:
  close position → record_exit() → IMMEDIATELY open reverse trade
                → bypass incubation AND cooldown

Why bypass both?
  The flip itself IS the incubation.  The 3-layer flip detector in monitor.py
  requires:
    - Layer 1: OFI flipped (bid/ask now favors opposite direction)
    - Layer 2: Delta exhaustion (momentum slowing)
    - Layer 3: Absorption confirmed (N ticks of consistent reversal)
  By the time all 3 layers pass, we've already observed 5-10 ticks of reversal
  confirmation.  Running the incubator again on top of that would add 60-120s
  of delay while the reversal move is already happening.

  Cooldown bypass: we're entering OPPOSITE direction.  Cooldown prevents
  re-entering the same direction on a falling stock (LODHA cascade).  A flip
  is the opposite — we WANT to enter immediately because we just confirmed
  the reversal.

REAL EXAMPLE — WHY GATE 3 (already holding) IS IN THE PIPELINE, NOT ENTRY
───────────────────────────────────────────────────────────────────────────
Gate 3 is "are we already holding this symbol?"  This is checked in the
pipeline, not in EntryPhase.  Why?

EntryPhase.open() checks Gate 3 (portfolio capacity) via order_manager.
But "portfolio capacity" is global (total active trades across all symbols).
"Already holding THIS symbol" is per-symbol.

If we have 1 active ADANIPOWER trade and MAX_ACTIVE_TRADES=2, the portfolio
status would say "can_open_new=True".  Without Gate 3 in the pipeline, the
scanner could fire a SECOND ADANIPOWER signal, pass through Entry's capacity
check, and open a second position in the same stock.

Gate 3 in the pipeline prevents that by checking specifically "is ADANIPOWER
in active trades?" — not just total count.

WHY main.py PASSES selected_stocks AND paused_stocks (NOT stored internally)
─────────────────────────────────────────────────────────────────────────────
These sets change at runtime via the FastAPI endpoints:
  POST /stocks/add, DELETE /stocks/remove → modifies selected_stocks
  POST /trading/pause → adds to paused_stocks
  POST /trading/resume → removes from paused_stocks

If the pipeline stored them internally, it would need update methods and
thread-safety mechanisms.  Passing them on each call keeps the pipeline
stateless w.r.t. user preferences — main.py owns the mutable state, pipeline
just reads it.

This design means: if a user pauses a stock at 10:00, the VERY NEXT TICK will
respect the pause.  No lag, no stale cache.

GATES 3 AND 4 — ALSO IN ENTRY, WHY THE DUPLICATION?
──────────────────────────────────────────────────────
Pipeline checks portfolio capacity (Gate 4) and already-holding (Gate 3)
BEFORE running the scanner.  Entry checks capacity again inside open().

The pipeline check is an OPTIMISATION — we skip the scanner/incubator CPU work
if we know we can't enter anyway.  The Entry check is the AUTHORITATIVE gate
for correctness.  They serve different purposes.

The scanner is cheap (a few dict lookups), but the incubator runs tick history
analysis.  Skipping both when the portfolio is full avoids unnecessary DB reads.
"""

from typing import Set

from core.logger import log_event
from data.database import db
from strategy.phases.entry import EntryPhase
from strategy.phases.incubator import Incubator
from strategy.phases.monitor import Monitor
from strategy.phases.scanner import Scanner
from strategy.types import ConfirmedSignal, ExitDecision
from trading.order_manager import order_manager
from trading.paper_trader import paper_trader


class TradePipeline:
    """
    The single orchestrator instance.  Lives in main.py as a module-level
    singleton.  All phase state (incubation state, peak prices, cooldown
    timers) lives inside the phase instances owned by this class.

    Thread safety: Zerodha sends ticks sequentially per symbol on a single
    WebSocket callback thread.  No locking needed unless we parallelise
    tick processing (we don't currently).
    """

    def __init__(self):
        self.scanner = Scanner()
        self.incubator = Incubator()
        self.entry = EntryPhase()
        self.monitor = Monitor()

    # ── main entry point ─────────────────────────────────────────────────────

    def on_tick(
        self,
        tick_data: dict,
        selected_stocks: Set[str],
        paused_stocks: Set[str],
    ) -> None:
        """
        Process one tick for one symbol.  Called from main.py on every
        WebSocket message.  Returns None — all side effects (orders, logs)
        happen internally.

        Parameters
        ──────────
        tick_data       : Raw tick from Zerodha WebSocket.  Expected keys:
                            symbol    — NSE trading symbol, e.g. 'ADANIPOWER'
                            ltp       — Last traded price
                            bid_qty   — Level-1 bid queue depth (shares)
                            ask_qty   — Level-1 ask queue depth (shares)
                            volume    — Cumulative day volume
                            timestamp — Server-side tick timestamp

        selected_stocks : The current watchlist.  Set of symbols the user
                          added via POST /stocks/add.  Only symbols in this
                          set are evaluated for new entries.

        paused_stocks   : Symbols temporarily paused for entries.  Set by
                          POST /trading/pause-symbol.  Monitor still runs for
                          paused symbols (exits fire normally).

        IMPORTANT: tick_history is fetched ONCE per tick and shared by all
        phases.  This ensures Scanner, Incubator, and Monitor all see the
        same market snapshot for this tick — no inconsistency from fetching
        at different times.
        """
        symbol = tick_data.get("symbol")
        current_price = tick_data.get("ltp", 0)
        bid_qty = tick_data.get("bid_qty", 0)
        ask_qty = tick_data.get("ask_qty", 0)

        if not symbol or not current_price:
            return

        # Log every tick for visibility — operator can see the stream in the
        # activity log.  The ratio here is the raw bid/ask (not display-flipped)
        # because this is just a heartbeat log, not a signal log.
        ratio = round(bid_qty / ask_qty, 2) if ask_qty else 0
        log_event(
            "info",
            f"{symbol}  ₹{current_price}  bid={bid_qty}  ask={ask_qty}  ratio={ratio}x",
            symbol=symbol,
        )

        # Fetch tick history ONCE — shared by Monitor, Scanner, and Incubator.
        # limit=100 gives ~15-20 minutes of ticks at typical Zerodha tick rates
        # (5-8 ticks/minute for mid-cap NSE stocks).
        # The Incubator needs ~5 min lookback for the trend gate = ~30-40 ticks.
        # 100 rows is a comfortable margin without being wasteful.
        tick_history = db.get_recent_ticks(symbol, limit=100)

        # ── Phase 4: Monitor — ALWAYS runs first ──────────────────────────────
        # See module docstring for why Monitor runs before entry gates.
        # Monitor checks: stop loss, profit trail, time exits, momentum flip.
        # If any exit condition fires, we close and return — no entry this tick.
        exit_decision = self.monitor.evaluate(symbol, current_price, tick_history)
        if exit_decision:
            self._execute_exit(exit_decision, tick_data, tick_history)
            return  # don't evaluate entry on the same tick as an exit

        # ── Gate 1: watchlist ─────────────────────────────────────────────────
        # Don't run the scanner on random ticks from other symbols.
        # Zerodha sends ticks for ALL subscribed instruments; we subscribe to
        # more than just the current day's watchlist (e.g. indices for reference).
        if symbol not in selected_stocks:
            return

        # ── Gate 2: per-symbol pause ──────────────────────────────────────────
        # User paused this symbol for new entries (not global trading pause).
        # Monitor still ran above — existing positions still get exited.
        if symbol in paused_stocks:
            return

        # ── Gate 3: already holding this symbol ──────────────────────────────
        # Don't open a second position in the same symbol.
        # See module docstring for why this gate is here and not only in Entry.
        active = [t for t in db.get_active_trades() if t["symbol"] == symbol]
        if active:
            return

        # ── Gate 4: portfolio capacity ────────────────────────────────────────
        # Fast-path: if portfolio is full, skip scanner + incubator entirely.
        # EntryPhase.open() also checks this, but running the scanner needlessly
        # costs DB reads.  This gate is an optimisation; Entry's check is the
        # authoritative one.
        if not order_manager.get_portfolio_status()["can_open_new"]:
            return

        # ── Phase 1: Scanner — evaluate for OFI signal ────────────────────────
        # Scanner applies:
        #   1. OFI threshold check (bid/ask ratio > BID_ASK_THRESHOLD_RATIO)
        #   2. Signal quality filter (5 checks: thin book, extreme ratio,
        #      iceberg suspect, buildup, weaker-side depth)
        # Returns CandidateSignal or None.
        candidate = self.scanner.evaluate(symbol, tick_data, tick_history)
        if candidate:
            log_event(
                "signal",
                f"{symbol} → {candidate.action} signal  ratio={candidate.ratio:.2f}x",
                symbol=symbol,
            )

        # ── Phase 2: Incubator — confirm signal over time ─────────────────────
        # Incubator does NOT require a CandidateSignal on every tick.
        # If candidate is None but the symbol is already being watched (a
        # previous tick started incubation), the incubator continues to
        # evaluate the in-progress incubation.
        #
        # Returns ConfirmedSignal when criteria met, None otherwise.
        # See incubator.py for full tick-by-tick state machine.
        confirmed = self.incubator.update(symbol, candidate, current_price, tick_history)
        if confirmed is None:
            return

        # ── Phase 3: Entry — open position if all gates pass ─────────────────
        # Entry applies:
        #   Gate 1: is trading globally paused for today?
        #   Gate 2: is symbol in cooldown (< 60s since last exit)?
        #   Gate 3: portfolio at capacity?
        # Then places the order and returns OpenPosition.
        self.entry.open(confirmed, current_price)

    # ── exit execution ────────────────────────────────────────────────────────

    def _execute_exit(
        self,
        decision: ExitDecision,
        tick_data: dict,
        tick_history: list,
    ) -> None:
        """
        Close the position and handle any follow-on logic.

        Two paths:
          MOMENTUM_FLIP  — close current, immediately open reverse
          everything else — close and record (cooldown clock starts)

        WHY _execute_exit() IS SEPARATE FROM on_tick()
        ────────────────────────────────────────────────
        The flip path needs to construct a ConfirmedSignal from scratch (without
        running the incubator) and call entry.open_flip().  Keeping this logic
        in a separate method keeps on_tick() readable and makes the two exit
        paths easy to compare side-by-side.

        REAL EXAMPLE — MOMENTUM_FLIP (2026-03-25, PATANJALI)
        ──────────────────────────────────────────────────────
        Hypothetical: PATANJALI SELL position at ₹1,580, then 3-layer flip
        detector fires at ₹1,590 (price bounced, bid pressure returned).
          1. paper_trader.close_order(trade_id, 1590, 'MOMENTUM_FLIP')
          2. monitor.clear_position(trade_id)  — peak/flip state cleaned up
          3. entry.record_exit(symbol)  — cooldown clock starts (but will be
             bypassed by the following open_flip call)
          4. reverse_action = 'BUY'  (was SELL)
          5. Construct ConfirmedSignal with current tick's bid/ask and meta
          6. entry.open_flip(flip_confirmed, 1590)  — Gate 2 (cooldown) skipped

        The result: we closed SELL @ 1590, opened BUY @ 1590 in one tick.
        No incubation wait, no cooldown wait — the flip IS the confirmation.
        """
        symbol = decision.symbol
        current_price = decision.exit_price
        trade_id = decision.trade_id

        if decision.reason == "MOMENTUM_FLIP":
            # Close current position
            paper_trader.close_order(trade_id, current_price, "MOMENTUM_FLIP")
            log_event(
                "trade",
                f"MOMENTUM FLIP: closed {decision.direction} {symbol} @ ₹{current_price}",
                symbol=symbol,
            )
            self.monitor.clear_position(trade_id)
            self.entry.record_exit(symbol)

            # Immediately open reverse trade — bypasses incubation and cooldown.
            # See module docstring ("MOMENTUM_FLIP — THE SPECIAL EXIT") for full
            # rationale on why both bypasses are intentional here.
            reverse_action = "BUY" if decision.direction == "SELL" else "SELL"
            bid_qty = tick_data.get("bid_qty", 0)
            ask_qty = tick_data.get("ask_qty", 0)

            # Construct ConfirmedSignal directly — incubation already happened
            # inside the 3-layer flip detector (monitor.py).
            # signal_meta is captured at flip time for ML training data.
            flip_confirmed = ConfirmedSignal(
                symbol=symbol,
                action=reverse_action,
                entry_price=current_price,
                ratio=Scanner._display_ratio(bid_qty, ask_qty, reverse_action),
                bid_qty=bid_qty,
                ask_qty=ask_qty,
                signal_meta=Scanner._build_meta(
                    {"action": reverse_action, "confidence": 1.0},
                    tick_history,
                    bid_qty,
                    ask_qty,
                ),
            )
            self.entry.open_flip(flip_confirmed, current_price)

        else:
            # Normal exit: close, log, clean up state.
            # record_exit() starts the per-symbol cooldown clock (60s).
            # The next entry attempt for this symbol will be blocked by
            # EntryPhase._in_cooldown() until 60s has elapsed.
            paper_trader.close_order(trade_id, current_price, decision.reason)
            log_event(
                "trade",
                f"CLOSED {decision.direction} {symbol} @ ₹{current_price} | {decision.reason}",
                symbol=symbol,
            )
            self.monitor.clear_position(trade_id)
            self.entry.record_exit(symbol)

    # ── housekeeping ──────────────────────────────────────────────────────────

    def reset_symbol(self, symbol: str) -> None:
        """
        Clear all incubation state for a symbol.

        Called by main.py when a symbol is removed from the watchlist
        (DELETE /stocks/remove).  Without this call, if a symbol is removed
        mid-incubation and then re-added, it could resume an old incubation
        watch from before the removal — which would reference stale trigger_price
        and tick timestamps.

        Example: LODHA is incubating a BUY signal at ₹758.  User removes LODHA.
        10 minutes later user re-adds LODHA (stock is now at ₹745).
        Without reset_symbol(), the incubator would still have trigger_price=758
        and see "price moved -₹13 against BUY signal" → would eventually time out.
        With reset_symbol(), the slate is clean and a fresh signal can form.

        Does NOT clear monitor state (peak prices, flip state) — if there's
        somehow an active position in a symbol being removed, exits should still
        fire.  That scenario shouldn't happen in practice (you'd close the
        position before removing from watchlist), but it's safer to leave
        monitor state intact.
        """
        self.incubator.clear(symbol)
