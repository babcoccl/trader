import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(cleanup);

// jsdom has no EventSource; the StatusStrip opens /api/stream.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, EventListener[]>();
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  emit(type: string, data: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const l of this.listeners.get(type) ?? []) l(event);
  }
  close() {
    this.closed = true;
  }
}
vi.stubGlobal("EventSource", FakeEventSource);

// jsdom has no ResizeObserver; recharts' ResponsiveContainer needs it.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
