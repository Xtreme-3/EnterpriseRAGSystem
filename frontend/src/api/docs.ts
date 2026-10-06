import client from "./client";

/** 摄取进度（K4）。stage 为 pending/parsing/chunking/embedding/indexing/done/failed。 */
export interface JobStatus {
  stage: string;
  done_units: number;
  total_units: number; // chunking 完成后才有值；为 0 时前端显示不定态
  error?: string | null;
}

export interface DocItem {
  id: number;
  kb_id: number;
  filename: string;
  file_type: string;
  status: string;
  chunk_count: number;
  created_at: string;
  error?: string | null;
  /** K4：异步摄取进度。同步摄取的文档为 null。 */
  job?: JobStatus | null;
}

export const docApi = {
  list(kbId: number): Promise<DocItem[]> {
    return client.get(`/kbs/${kbId}/documents`).then((r) => r.data);
  },

  /** 上传：服务端**立即**返回 202 + status=pending，向量化在后台跑（K4）。 */
  upload(kbId: number, file: File): Promise<DocItem> {
    const form = new FormData();
    form.append("file", file);
    return client
      .post(`/kbs/${kbId}/documents`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },

  /** 重新摄取一份 failed 的文档（K4）。非 failed 后端返回 409。 */
  retry(docId: number): Promise<DocItem> {
    return client.post(`/documents/${docId}/retry`).then((r) => r.data);
  },

  remove(docId: number): Promise<void> {
    return client.delete(`/documents/${docId}`).then((r) => r.data);
  },
};
