#!/usr/bin/env node
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ErrorCode,
  ListToolsRequestSchema,
  McpError,
} from '@modelcontextprotocol/sdk/types.js';

const CONFIG = {
  embeddingApiUrl: process.env.EMBEDDING_API_URL || 'https://api.perplexity.ai/embeddings',
  embeddingApiKey: process.env.EMBEDDING_API_KEY || '',
  embeddingModel: process.env.EMBEDDING_MODEL || 'pplx-embed-v1-0.6b',
  qdrantUrl: process.env.QDRANT_URL || 'http://localhost:6333',
  qdrantCollection: process.env.QDRANT_COLLECTION || 'ws-5e70e849fd3d1c12',
};

interface SearchArgs {
  query: string;
  limit?: number;
}

function isValidSearchArgs(args: any): args is SearchArgs {
  return (
    typeof args === 'object' &&
    args !== null &&
    typeof args.query === 'string' &&
    args.query.length > 0 &&
    (args.limit === undefined || typeof args.limit === 'number')
  );
}

class QdrantCodeSearchServer {
  private server: Server;

  constructor() {
    this.server = new Server(
      { name: 'qdrant-code-search', version: '0.1.0' },
      { capabilities: { tools: {} } }
    );

    this.setupToolHandlers();

    this.server.onerror = (error) => console.error('[QdrantCodeSearch Error]', error);
    process.on('SIGINT', async () => {
      await this.server.close();
      process.exit(0);
    });
  }

  private async getEmbedding(text: string): Promise<number[]> {
    const response = await fetch(CONFIG.embeddingApiUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${CONFIG.embeddingApiKey}`,
      },
      body: JSON.stringify({
        model: CONFIG.embeddingModel,
        input: text,
      }),
    });

    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Embedding API error ${response.status}: ${body}`);
    }

    const data = await response.json() as { data: Array<{ embedding: number[] }> };
    return data.data[0].embedding;
  }

  private async searchQdrant(vector: number[], limit: number) {
    const url = `${CONFIG.qdrantUrl}/collections/${CONFIG.qdrantCollection}/points/search`;
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vector, limit, with_payload: true, with_vector: false }),
    });

    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Qdrant API error ${response.status}: ${body}`);
    }

    const data = await response.json() as { result: any[] };
    return data.result;
  }

  private setupToolHandlers() {
    this.server.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: [
        {
          name: 'search_code',
          description:
            'Семантический поиск по кодовой базе проекта (1С BSL, XML, Python). ' +
            'Использует Perplexity embeddings (pplx-embed-v1-0.6b, 1024d) и Qdrant. ' +
            'Возвращает релевантные фрагменты кода с путями к файлам и номерами строк.',
          inputSchema: {
            type: 'object',
            properties: {
              query: {
                type: 'string',
                description: 'Поисковый запрос на естественном языке или фрагмент кода',
              },
              limit: {
                type: 'number',
                description: 'Количество результатов (по умолчанию 5, максимум 20)',
                default: 5,
              },
            },
            required: ['query'],
          },
        },
      ],
    }));

    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      const { name, arguments: args } = request.params;

      if (name !== 'search_code') {
        throw new McpError(ErrorCode.MethodNotFound, `Unknown tool: ${name}`);
      }

      if (!isValidSearchArgs(args)) {
        throw new McpError(
          ErrorCode.InvalidParams,
          'Invalid arguments: "query" (string) is required, "limit" (number) is optional'
        );
      }

      const limit = Math.min(args.limit || 5, 20);

      try {
        console.error(`[QdrantCodeSearch] Embedding: "${args.query.slice(0, 80)}"`);
        const embedding = await this.getEmbedding(args.query);

        console.error(`[QdrantCodeSearch] Searching Qdrant (limit=${limit})...`);
        const results = await this.searchQdrant(embedding, limit);

        const formatted = results.map((r: any, i: number) => {
          const p = r.payload || {};
          const path = p.filePath
            || (p.pathSegments ? Object.values(p.pathSegments).join('/') : null)
            || '(путь не указан)';
          const lines = p.startLine ? ` (строки ${p.startLine}-${p.endLine || p.startLine})` : '';
          const chunk = p.codeChunk ? p.codeChunk.slice(0, 500) : '(нет фрагмента)';

          return [
            `#${i + 1} [score: ${r.score.toFixed(4)}] ${path}${lines}`,
            '```',
            chunk,
            '```',
          ].join('\n');
        });

        const text = results.length > 0
          ? `Найдено ${results.length} результатов:\n\n${formatted.join('\n\n')}`
          : 'Ничего не найдено.';

        return { content: [{ type: 'text', text }] };
      } catch (error) {
        if (error instanceof McpError) throw error;
        return {
          content: [
            {
              type: 'text',
              text: `Ошибка поиска: ${error instanceof Error ? error.message : String(error)}`,
            },
          ],
          isError: true,
        };
      }
    });
  }

  async run() {
    const transport = new StdioServerTransport();
    await this.server.connect(transport);
    console.error('Qdrant Code Search MCP server running on stdio');
  }
}

const server = new QdrantCodeSearchServer();
server.run().catch(console.error);
