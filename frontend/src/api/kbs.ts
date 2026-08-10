import client from "./client";

export interface KBItem {
  id: number;
  name: string;
  description: string;
  created_at: string;
  document_count: number;
}

export interface KBCreateRequest {
  name: string;
  description?: string;
}

export const kbApi = {
  list(): Promise<KBItem[]> {
    return client.get("/kbs").then((r) => r.data);
  },

  get(id: number): Promise<KBItem> {
    return client.get(`/kbs/${id}`).then((r) => r.data);
  },

  create(data: KBCreateRequest): Promise<KBItem> {
    return client.post("/kbs", data).then((r) => r.data);
  },

  remove(id: number): Promise<void> {
    return client.delete(`/kbs/${id}`).then((r) => r.data);
  },
};
