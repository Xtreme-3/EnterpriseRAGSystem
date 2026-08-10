import client from "./client";

export interface Summary {
  total_documents: number;
  indexed: number;
  failed: number;
  processing: number;
  pending: number;
  total_chunks: number;
}

export interface FailureDoc {
  id: number;
  filename: string;
}

export interface FailureGroup {
  error: string;
  count: number;
  documents: FailureDoc[];
}

export interface AnomalyDoc {
  id: number;
  filename: string;
  status: string;
  chunk_count: number;
  error: string | null;
}

export type ConsistencyKind = "missing_index" | "count_drift" | "orphan_vector";

export interface ConsistencyIssue {
  kind: ConsistencyKind;
  document_id: number;
  filename: string | null;
  meta_count: number;
  vector_count: number;
}

export interface Consistency {
  checked: boolean;
  issues: ConsistencyIssue[];
}

export interface DiagnosticsResponse {
  kb_id: number;
  summary: Summary;
  failures: FailureGroup[];
  anomalies: AnomalyDoc[];
  consistency: Consistency;
}

export const diagnosticsApi = {
  get(kbId: number): Promise<DiagnosticsResponse> {
    return client.get(`/kbs/${kbId}/diagnostics`).then((r) => r.data);
  },
};
