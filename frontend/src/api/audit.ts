import client from "./client";

export interface AuditLogItem {
  id: number;
  action: string;
  actor_id: number | null;
  actor_name: string;
  kb_id: number | null;
  target: string;
  detail: string;
  created_at: string;
}

export interface AuditLogPage {
  items: AuditLogItem[];
  total: number;
}

export interface AuditQuery {
  action?: string;
  actor_id?: number;
  kb_id?: number;
  limit?: number;
  offset?: number;
}

export const auditApi = {
  list(query: AuditQuery = {}): Promise<AuditLogPage> {
    return client.get("/audit-logs", { params: query }).then((r) => r.data);
  },
};
