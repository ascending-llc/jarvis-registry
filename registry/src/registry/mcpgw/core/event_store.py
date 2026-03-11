from collections import deque
from dataclasses import dataclass
from uuid import uuid4

from mcp.server.streamable_http import EventCallback, EventId, EventMessage, EventStore, StreamId
from mcp.types import JSONRPCMessage


@dataclass
class EventEntry:
    event_id: EventId
    stream_id: StreamId
    message: JSONRPCMessage | None


class InMemoryEventStore(EventStore):
    def __init__(self, max_events_per_stream: int = 100, max_streams: int = 1000):
        self.max_events_per_stream = max_events_per_stream
        self.max_streams = max_streams
        self.streams: dict[StreamId, deque[EventEntry]] = {}
        self.event_index: dict[EventId, EventEntry] = {}
        self.stream_order: deque[StreamId] = deque()  # LRU tracking - oldest at front

    async def store_event(self, stream_id: StreamId, message: JSONRPCMessage | None) -> EventId:
        event_id = str(uuid4())
        event_entry = EventEntry(event_id=event_id, stream_id=stream_id, message=message)

        # If we're at capacity and this is a new stream, evict the oldest stream
        if stream_id not in self.streams and len(self.streams) >= self.max_streams:
            oldest_stream_id = self.stream_order.popleft()
            # Clean up all events from the oldest stream
            if oldest_stream_id in self.streams:
                for event in self.streams[oldest_stream_id]:
                    self.event_index.pop(event.event_id, None)
                del self.streams[oldest_stream_id]

        if stream_id not in self.streams:
            self.streams[stream_id] = deque(maxlen=self.max_events_per_stream)
            self.stream_order.append(stream_id)
        else:
            # Move to end (most recently used)
            self.stream_order.remove(stream_id)
            self.stream_order.append(stream_id)

        # Remove oldest event from index if deque is full
        if len(self.streams[stream_id]) == self.max_events_per_stream:
            oldest_event = self.streams[stream_id][0]
            self.event_index.pop(oldest_event.event_id, None)

        self.streams[stream_id].append(event_entry)
        self.event_index[event_id] = event_entry
        return event_id

    async def replay_events_after(self, last_event_id: EventId, send_callback: EventCallback) -> StreamId | None:
        if last_event_id not in self.event_index:
            return None

        last_event = self.event_index[last_event_id]
        stream_id = last_event.stream_id
        stream_events = self.streams.get(stream_id, deque())

        # Update LRU on replay access
        if stream_id in self.stream_order:
            self.stream_order.remove(stream_id)
            self.stream_order.append(stream_id)

        # Collect events to remove (confirmed delivered) and events to replay
        events_to_remove: list[EventEntry] = []
        events_to_replay: list[tuple[EventId, JSONRPCMessage]] = []
        found_last = False

        for event in stream_events:
            if not found_last:
                if event.event_id == last_event_id:
                    found_last = True
                    # Keep the anchor event (last_event_id) for future replays
                else:
                    # These events are before last_event_id - client has them, can remove
                    events_to_remove.append(event)
            else:
                # These are events AFTER last_event_id - need to replay
                if event.message is not None:
                    events_to_replay.append((event.event_id, event.message))

        # Send events to replay
        for event_id, message in events_to_replay:
            await send_callback(EventMessage(message, event_id))

        # Clean up confirmed-delivered events
        for event in events_to_remove:
            self.event_index.pop(event.event_id, None)
            # Note: Can't efficiently remove from deque middle, but they'll be evicted by maxlen
            # If you need efficient removal, consider using a different data structure

        return stream_id
