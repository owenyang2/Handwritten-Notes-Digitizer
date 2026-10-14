import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mountShell, type ShellHandle, type ShellMounts } from "./AppShell.js";
import { getDocumentsPage, login, updateDocument } from "../api/client.js";

vi.mock("../api/client.js", () => ({
  ApiError: class extends Error {},
  login: vi.fn(),
  register: vi.fn(),
  getDocumentsPage: vi.fn(),
  updateDocument: vi.fn(),
  restoreDocument: vi.fn(),
  trashDocument: vi.fn(),
  getPageRasterUrl: (id: string) => `/api/pages/${id}/raster`,
  getProcessedImageUrl: (id: string) => `/api/images/${id}/processed`,
}));

describe("Library", () => {
  let root: HTMLElement;
  let shell: ShellHandle | undefined;
  const notebook = {
    document_id: "doc-1",
    title: "Lecture notes",
    created_at: "2026-10-12T12:00:00Z",
    image_ids: [],
  };
  beforeEach(() => {
    vi.resetAllMocks();
    window.history.replaceState(null, "", "/library");
    root = document.createElement("div");
    document.body.append(root);
    vi.mocked(getDocumentsPage).mockResolvedValue({
      items: [notebook],
      offset: 0,
      limit: 50,
      total: 1,
      hasMore: false,
    });
  });
  afterEach(() => {
    shell?.dispose();
    shell = undefined;
    root.remove();
  });
  const onReauthenticated = vi.fn();
  const mount = () => {
    const mounts = Object.fromEntries(
      [
        "upload",
        "batch",
        "editor",
        "math",
        "export",
        "calibration",
        "training",
        "sync",
        "privacy",
        "banner",
      ].map((name) => [name, document.createElement("section")]),
    ) as unknown as ShellMounts;
    shell = mountShell(root, {
      mounts,
      username: "Test",
      onOpen: vi.fn().mockResolvedValue(undefined),
      onLogout: vi.fn(),
      onReauthenticated,
      onCloseImport: vi.fn(),
    });
  };
  it("keeps typed credentials across re-renders and confirms the account after signing in again", async () => {
    window.history.replaceState(null, "", "/settings");
    onReauthenticated.mockResolvedValue(undefined);
    vi.mocked(login).mockResolvedValue(undefined as never);
    mount();
    const open = await vi.waitFor(() => {
      const found = [...root.querySelectorAll("button")].find(
        (item) => item.textContent === "Sign in again",
      );
      expect(found).toBeDefined();
      return found!;
    });
    open.click();
    const username = await vi.waitFor(() => {
      const input = document.querySelector<HTMLInputElement>(
        "[data-testid=auth-username]",
      );
      expect(input).not.toBeNull();
      return input!;
    });
    username.value = "Test";
    document.querySelector<HTMLInputElement>(
      "[data-testid=auth-password]",
    )!.value = "secret";
    const calls = vi.mocked(getDocumentsPage).mock.calls.length;
    shell!.refreshLibrary();
    await vi.waitFor(() =>
      expect(vi.mocked(getDocumentsPage).mock.calls.length).toBeGreaterThan(
        calls,
      ),
    );
    expect(
      document.querySelector<HTMLInputElement>("[data-testid=auth-username]")
        ?.value,
    ).toBe("Test");
    document
      .querySelector<HTMLFormElement>("[data-testid=auth-form]")!
      .requestSubmit();
    await vi.waitFor(() => expect(onReauthenticated).toHaveBeenCalledOnce());
    expect(login).toHaveBeenCalledWith("Test", "secret");
  });
  it("renders notebook titles as literal text and opens an addressable document", async () => {
    vi.mocked(getDocumentsPage).mockResolvedValue({
      items: [
        { ...notebook, title: '<img src=x onerror="window.injected=true">' },
      ],
      offset: 0,
      limit: 50,
      total: 1,
      hasMore: false,
    });
    mount();
    await vi.waitFor(() =>
      expect(root.querySelector(".gallery-document")).not.toBeNull(),
    );
    expect(root.querySelector("img[src=x]")).toBeNull();
    expect(root.textContent).toContain(
      '<img src=x onerror="window.injected=true">',
    );
    expect(root.querySelector(".gallery-document")?.getAttribute("href")).toBe(
      "/notebooks/doc-1",
    );
  });
  it("keeps a readable failure and retry action without injecting error markup", async () => {
    vi.mocked(getDocumentsPage).mockRejectedValue(
      new Error("<strong>Request failed</strong>"),
    );
    mount();
    await vi.waitFor(() =>
      expect(root.querySelector("[role=alert]")?.textContent).toContain(
        "<strong>Request failed</strong>",
      ),
    );
    expect(root.querySelector("[role=alert] strong")).toBeNull();
  });
  it("uses the server pagination contract when loading more notebooks", async () => {
    vi.mocked(getDocumentsPage).mockResolvedValueOnce({
      items: [notebook],
      offset: 0,
      limit: 1,
      total: 2,
      hasMore: true,
    });
    mount();
    const load = await vi.waitFor(() => {
      const found = [...root.querySelectorAll("button")].find(
        (item) => item.textContent === "Load more notebooks",
      );
      expect(found).toBeDefined();
      return found!;
    });
    vi.mocked(getDocumentsPage).mockResolvedValueOnce({
      items: [{ ...notebook, document_id: "doc-2" }],
      offset: 1,
      limit: 1,
      total: 2,
      hasMore: false,
    });
    load.click();
    await vi.waitFor(() =>
      expect(root.querySelectorAll(".gallery-document")).toHaveLength(2),
    );
    expect(getDocumentsPage).toHaveBeenLastCalledWith({
      view: "active",
      query: "",
      offset: 1,
    });
    expect(updateDocument).not.toHaveBeenCalled();
  });
});
