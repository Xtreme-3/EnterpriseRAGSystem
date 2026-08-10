import client from "./client";

export interface QaLogItem {
  id: number;
  query: string;
  answer: string;
  hit_doc_ids: number[];
  hit_count: number;
  created_at: string;
}

export const qaLogApi = {
  list(kbId: number, limit: number = 20): Promise<QaLogItem[]> {
    return client.get(`/kbs/${kbId}/qa-logs`, { params: { limit } }).then((r) => r.data);
  },
};
