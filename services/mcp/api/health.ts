import type { IncomingMessage, ServerResponse } from "node:http";

export default function handler(
  _request: IncomingMessage,
  response: ServerResponse & {
    status(code: number): ServerResponse & { json(value: unknown): void };
    json(value: unknown): void;
  },
): void {
  response.status(200).json({
    service: "stockscout-unified-mcp",
    version: "1.0.0",
    mode: "tool-only",
    data_source: "github-pages",
    auth_required: false,
    write_capability: false,
  });
}
