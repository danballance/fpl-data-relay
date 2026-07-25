import type { ChangeEvent } from "./types";

const REQUIRED_STRING_FIELDS = [
  "entity_family",
  "event_name",
  "payload_hash",
  "fetched_at",
  "created_at",
] as const;

export function isChangeEvent(value: unknown): value is ChangeEvent {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.id === "number" &&
    REQUIRED_STRING_FIELDS.every((field) => typeof record[field] === "string")
  );
}

export function parseSseFrame(frame: string): unknown | undefined {
  const dataLines = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart());
  if (dataLines.length === 0) {
    return undefined;
  }
  return JSON.parse(dataLines.join("\n")) as unknown;
}

export async function consumeSse({
  body,
  onEvent,
}: {
  body: ReadableStream<Uint8Array>;
  onEvent: (event: ChangeEvent) => void;
}): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const result = await reader.read();
    buffer += decoder.decode(result.value, { stream: !result.done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const parsed = parseSseFrame(frame);
      if (parsed === undefined) {
        continue;
      }
      if (!isChangeEvent(parsed)) {
        throw new Error("The relay stream returned an invalid change event.");
      }
      onEvent(parsed);
    }

    if (result.done) {
      if (buffer.trim() !== "") {
        const parsed = parseSseFrame(buffer);
        if (parsed !== undefined) {
          if (!isChangeEvent(parsed)) {
            throw new Error("The relay stream returned an invalid change event.");
          }
          onEvent(parsed);
        }
      }
      return;
    }
  }
}
