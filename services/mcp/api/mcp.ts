import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import type { IncomingMessage, ServerResponse } from "node:http";

import { createStockScoutServer } from "../src/mcpServer.js";

interface VercelRequest extends IncomingMessage {
  body?: unknown;
}

interface VercelResponse extends ServerResponse {
  status(code: number): VercelResponse;
  json(value: unknown): void;
}

export default async function handler(
  request: VercelRequest,
  response: VercelResponse,
): Promise<void> {
  if (request.method !== "POST") {
    response.setHeader("Allow", "POST");
    response.status(405).json({
      jsonrpc: "2.0",
      error: { code: -32000, message: "Method not allowed" },
      id: null,
    });
    return;
  }

  try {
    const server = createStockScoutServer();
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    });
    await server.connect(transport);
    response.on("close", () => {
      void transport.close();
      void server.close();
    });
    await transport.handleRequest(request, response, request.body);
  } catch (error) {
    if (!response.headersSent) {
      console.error(error);
      response.status(500).json({
        jsonrpc: "2.0",
        error: { code: -32603, message: "Unable to read the active public scan" },
        id: null,
      });
    }
  }
}
