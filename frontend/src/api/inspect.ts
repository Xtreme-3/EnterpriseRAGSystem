import client from "./client";

export interface InspectHit {
  document_id: number;
  filename: string;
  chunk_index: number;
  content: string;
  score: number;
}

export interface InspectResponse {
  query: string;
  kb_id: number;
  top_k: number;
  mode: string;
  hits: InspectHit[];
}

export const inspectApi = {
  inspect(
    kbId: number,
    query: string,
    topK: number = 0,
    mode: string = "hybrid"
  ): Promise<InspectResponse> {
    return client.post(`/kbs/${kbId}/inspect`, { query, top_k: topK, mode }).then((r) => r.data);
  },
};
