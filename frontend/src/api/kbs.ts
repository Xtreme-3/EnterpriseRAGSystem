import client from "./client";

export interface KBItem {
  id: number;
  name: string;
  description: string;
  created_at: string;
  document_count: number;
  /** 当前用户对此库的角色：owner | editor | viewer */
  role: string;
}

export interface KBCreateRequest {
  name: string;
  description?: string;
}

/** 知识库成员（owner 行 + 协作成员） */
export interface KBMember {
  user_id: number;
  username: string;
  role: string; // owner | editor | viewer
  created_at: string | null;
}

export interface MemberAddRequest {
  username: string;
  role: string; // editor | viewer
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

  // ---- I2 RBAC：成员管理 ----

  members(id: number): Promise<KBMember[]> {
    return client.get(`/kbs/${id}/members`).then((r) => r.data);
  },

  addMember(id: number, data: MemberAddRequest): Promise<KBMember> {
    return client.post(`/kbs/${id}/members`, data).then((r) => r.data);
  },

  updateMember(id: number, userId: number, role: string): Promise<KBMember> {
    return client.patch(`/kbs/${id}/members/${userId}`, { role }).then((r) => r.data);
  },

  removeMember(id: number, userId: number): Promise<void> {
    return client.delete(`/kbs/${id}/members/${userId}`).then((r) => r.data);
  },
};
