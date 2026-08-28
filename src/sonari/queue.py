from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class SpeechItem:
    id: int
    session: str
    kind: str          # one of prose|choice|plan|permission|tool_announce
    text: str
    is_decision: bool  # True for choice|plan|permission
    control_cue: bool = False  # the answer to a deliberate press: never
                               # folder-prefixed, and voiced by the held branch
                               # even while its stream is stopped
    names_session: bool = False  # text already speaks the session's folder (jump cue)
    audio_path: "str | None" = None  # when set, the speak loop afplays this file (spearcon) instead of say
    forward: bool = False  # SP4 provenance: True only at forward-readout enqueue sites (prose/decision/
                           # tool-announce readout). Browse-replay + control cues stay False so a review
                           # gesture never advances the frontier (B1). Read only by note_spoken's advance.
    voice: "str | None" = None  # SP5: per-utterance say voice override (the summary body); None == main voice
    render_id: "int | None" = None  # SP5: groups a catch-up render's frame/body/tail items
    catchup_burn: bool = False  # SP5: True on the render's LAST item; note_spoken burns on its completion
    prelude: tuple = ()  # D8: audio paths the loop plays BEFORE text/audio_path, as
                         # one atomic unit (same claim + cancel epoch; an interrupted
                         # unit replays whole). Not persisted: to_state() serializes
                         # only {frontier}, so no state-version bump.


class SpeechQueue:
    def __init__(self, cap: "int | None" = None) -> None:
        self._items: "deque[SpeechItem]" = deque()
        self._cap = cap                      # None == unbounded backlog

    def enqueue(self, item: SpeechItem) -> "SpeechItem | None":
        """Append *item*. When a cap is set and the queue is full, evict and RETURN the
        oldest NON-decision item so the caller can drop its pending-heard marker (a
        backgrounded stream must stay memory-bounded). Decision items (plan/choice/
        permission) are EXEMPT — never silently dropped, so a waiting prompt is preserved;
        if every queued item is a decision, the queue is allowed to exceed the cap rather
        than drop one. Returns the evicted item, or None."""
        evicted = None
        if self._cap is not None and len(self._items) >= self._cap:
            for i, it in enumerate(self._items):
                if not it.is_decision:
                    evicted = it
                    del self._items[i]
                    break
            # all-decisions: nothing evictable; the queue temporarily exceeds the cap
        self._items.append(item)
        return evicted

    def enqueue_front(self, item: SpeechItem) -> None:
        """Put *item* at the head — used to resume the utterance paused mid-play.
        Not subject to the cap: it re-inserts an item just popped from this queue."""
        self._items.appendleft(item)

    def pop_next(self) -> "SpeechItem | None":
        # The speak loop pops outside the daemon lock while clear() can swap the
        # deque underneath it; treat a lost race as "nothing to say" rather than
        # letting an IndexError kill the speak loop (a mute daemon).
        try:
            return self._items.popleft()
        except IndexError:
            return None

    def pop_control_cue(self) -> "SpeechItem | None":
        """Pop the first control-cue item from ANYWHERE in the queue, else None.

        While paused the loop holds, but a pause confirmation ("Paused.") must still
        be voiced. It is found by scanning rather than peeking the head: a pause
        landing mid-utterance re-queues the interrupted (non-exempt) item at the
        front, so the control cue is not necessarily first."""
        for i, item in enumerate(self._items):
            if item.control_cue:
                del self._items[i]
                return item
        return None

    def jump_to_decision(self) -> "list[SpeechItem]":
        """Discard leading non-decision items so the next decision is at the front.
        Returns the discarded items so the caller can drop their heard-markers."""
        dropped = []
        while self._items and not self._items[0].is_decision:
            dropped.append(self._items.popleft())
        return dropped

    def has_decision(self) -> bool:
        """True if any queued item is a decision (choice|plan|permission). Used to
        rank a waiting session ahead of prose-only ones for jump-to-waiting."""
        return any(item.is_decision for item in self._items)

    def clear(self) -> "list[SpeechItem]":
        dropped = list(self._items)
        self._items.clear()
        return dropped

    def __len__(self) -> int:
        return len(self._items)

    def oldest_id(self) -> "int | None":
        """The id of the head (oldest) item, or None when empty. Non-destructive — the
        keep-going selector compares queue ages WITHOUT popping. Keeps _items private;
        the smallest surviving id across all queues is the oldest unheard output (the id
        counter is daemon-global and monotonic)."""
        return self._items[0].id if self._items else None

    def oldest_control_cue_id(self) -> "int | None":
        """Id of the oldest queued control cue, without removing it."""
        for item in self._items:
            if item.control_cue:
                return item.id
        return None

    def remove_by_id(self, item_id: int) -> "SpeechItem | None":
        """Remove and return the queued item with id *item_id*, else None. The
        chooser swaps out its still-queued previous preview before enqueuing the
        next one (each preview replaces + barge-ins the last)."""
        for i, item in enumerate(self._items):
            if item.id == item_id:
                del self._items[i]
                return item
        return None

    def remove_where(self, pred) -> "list[SpeechItem]":
        """Remove and return every queued item matching *pred* (SpeechItem -> bool).
        The catch-up burn drops the pinned pile; a cut drops the render's siblings.
        Caller holds the daemon lock."""
        kept, removed = deque(), []
        for it in self._items:
            (removed if pred(it) else kept).append(it)
        self._items = kept
        return removed

    def insert_after(self, item_id, items) -> bool:
        """Insert *items* (in order) immediately after the queued item with id
        *item_id*. Returns False when that item is no longer queued (already
        spoken/dropped) so the caller can fall back to at_front. The catch-up
        render lands after its still-queued ack this way. Caller holds the lock."""
        for i, it in enumerate(self._items):
            if it.id == item_id:
                for offset, new in enumerate(items, start=1):
                    self._items.insert(i + offset, new)
                return True
        return False
