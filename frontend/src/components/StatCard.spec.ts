import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import StatCard from "./StatCard.vue";

/** StatCard（Known Gap #7 抽取）：数字 + 标签 + 可选语义色，两看板页共用的契约。 */
describe("StatCard", () => {
  it("渲染数字与标签，默认无语气色", () => {
    const w = mount(StatCard, { props: { value: 12, label: "总文档" } });
    expect(w.find(".stat-num").text()).toBe("12");
    expect(w.find(".stat-label").text()).toBe("总文档");
    expect(w.find(".stat-card").classes()).not.toContain("stat-card--good");
  });

  it("tone 映射为语义色 class（good / bad / accent）", () => {
    const good = mount(StatCard, { props: { value: 1, label: "a", tone: "good" } });
    const bad = mount(StatCard, { props: { value: 2, label: "b", tone: "bad" } });
    const accent = mount(StatCard, { props: { value: "87%", label: "命中率", tone: "accent" } });
    expect(good.find(".stat-card").classes()).toContain("stat-card--good");
    expect(bad.find(".stat-card").classes()).toContain("stat-card--bad");
    expect(accent.find(".stat-card").classes()).toContain("stat-card--accent");
    expect(accent.find(".stat-num").text()).toBe("87%");
  });
});
