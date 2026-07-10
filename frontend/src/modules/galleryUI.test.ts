import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client.js", () => ({
  getDocuments: vi.fn(),
}));

import { getDocuments } from "../api/client.js";
import { GalleryUI } from "./galleryUI.js";

describe("GalleryUI", () => {
  let mount: HTMLElement;

  beforeEach(() => {
    mount = document.createElement("div");
    mount.id = "gallery";
    document.body.appendChild(mount);
  });

  afterEach(() => {
    mount.remove();
    vi.resetAllMocks();
  });

  it("renders document titles as text and selects the requested document", async () => {
    vi.mocked(getDocuments).mockResolvedValue([
      {
        document_id: "doc-1",
        title: '<img src=x onerror="window.injected=true">',
        created_at: "2026-07-01T12:00:00Z",
        image_ids: ["image-1", "image-2"],
      },
    ]);
    const onSelectDocument = vi.fn();
    const gallery = new GalleryUI("gallery", onSelectDocument);

    await gallery.render();

    expect(mount.querySelector("img")).toBeNull();
    expect(mount.textContent).toContain('<img src=x onerror="window.injected=true">');
    const button = mount.querySelector<HTMLButtonElement>(".gallery-document")!;
    button.click();
    expect(onSelectDocument).toHaveBeenCalledWith("doc-1");
  });

  it("renders a readable error without injecting error content as markup", async () => {
    vi.mocked(getDocuments).mockRejectedValue(new Error("<strong>Request failed</strong>"));
    const gallery = new GalleryUI("gallery", vi.fn());

    await gallery.render();

    expect(mount.querySelector("strong")).toBeNull();
    expect(mount.textContent).toContain("<strong>Request failed</strong>");
  });
});
