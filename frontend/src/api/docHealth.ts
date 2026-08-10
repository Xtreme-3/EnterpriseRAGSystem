import client from "./client";

export interface DeadDoc {
  id: number;
  filename: string;
  status: string;
  chunk_count: number;
  hit_count: number;
  error: string | null;
}

export interface HotDoc {
  id: number;
  filename: string;
  hit_count: number;
  last_hit_at: string | null;
}

export interface DocHealthSummary {
  total_documents: number;
  indexed: number;
  active_count: number;
  dead_count: number;
  total_questions: number;
  hit_rate: number;
}

export interface DocHealthResponse {
  kb_id: number;
  summary: DocHealthSummary;
  dead_docs: DeadDoc[];
  hot_docs: HotDoc[];
}

export const docHealthApi = {
  get(kbId: number, limit = 10): Promise<DocHealthResponse> {
    return client.get(`/kbs/${kbId}/doc-health`, { params: { limit } }).then((r) => r.data);
  },
};
