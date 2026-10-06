/** G6 数据看板：GET /api/kbs/{id}/dashboard 的一次性聚合（后端一次返回全部块）。 */

export interface DashboardTopQuestion {
  query: string;
  /** qa_logs 中的提问次数 */
  count: number;
  /** 该问法在 query_cache 中的累计命中（未缓存为 0） */
  cache_hits: number;
}

export interface DashboardReason {
  reason: string;
  count: number;
}

export interface DashboardDeadDoc {
  filename: string;
  status: string;
}

export interface Dashboard {
  kb_id: number;
  total_questions: number;
  cache_hits: number;
  /** cache_hits / total_questions（含多轮等不缓存提问，诚实口径） */
  cache_hit_rate: number;
  feedback_total: number;
  feedback_up: number;
  feedback_up_rate: number;
  total_documents: number;
  dead_documents: number;
  top_questions: DashboardTopQuestion[];
  reason_breakdown: DashboardReason[];
  dead_doc_list: DashboardDeadDoc[];
}

import client from "./client";

export const dashboardApi = {
  get(kbId: number): Promise<Dashboard> {
    return client.get(`/kbs/${kbId}/dashboard`).then((r) => r.data);
  },
};
