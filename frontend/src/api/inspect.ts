import client from "./client";

export interface InspectHit {
  document_id: number;
  filename: string;
  chunk_index: number;
  content: string;
  score: number;
  /** 重排前检索分（未重排时与 score 相同），I1 前后对比 */
  pre_score: number;
}

export interface InspectResponse {
  query: string;
  kb_id: number;
  top_k: number;
  mode: string;
  /** 实际是否重排 */
  rerank: boolean;
  hits: InspectHit[];
}

export const inspectApi = {
  inspect(
    kbId: number,
    query: string,
    topK: number = 0,
    mode: string = "hybrid",
    rerank: boolean = false
  ): Promise<InspectResponse> {
    return client
      .post(`/kbs/${kbId}/inspect`, { query, top_k: topK, mode, rerank })
      .then((r) => r.data);
  },
};
