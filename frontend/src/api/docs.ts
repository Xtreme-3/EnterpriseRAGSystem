import client from "./client";

export interface DocItem {
  id: number;
  kb_id: number;
  filename: string;
  file_type: string;
  status: string;
  chunk_count: number;
  created_at: string;
  error?: string | null;
}

export const docApi = {
  list(kbId: number): Promise<DocItem[]> {
    return client.get(`/kbs/${kbId}/documents`).then((r) => r.data);
  },

  upload(kbId: number, file: File): Promise<DocItem> {
    const form = new FormData();
    form.append("file", file);
    return client
      .post(`/kbs/${kbId}/documents`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },

  remove(docId: number): Promise<void> {
    return client.delete(`/documents/${docId}`).then((r) => r.data);
  },
};
