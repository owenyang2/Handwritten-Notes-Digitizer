import {
  getDocuments,
  restoreDocument,
  trashDocument,
  updateDocument,
} from "../api/client.js";
import type { DocumentResponse } from "../api/client.js";

type DocumentView = "active" | "archived" | "trash";

export class GalleryUI {
  private container: HTMLElement;
  private onSelectDocument: (docId: string) => void;
  private query = "";
  private view: DocumentView = "active";

  constructor(containerId: string, onSelectDocument: (docId: string) => void) {
    const el = document.getElementById(containerId);
    if (!el) throw new Error(`Container #${containerId} not found`);
    this.container = el;
    this.onSelectDocument = onSelectDocument;
  }

  public async render(): Promise<void> {
    this.container.replaceChildren(
      this.createHeading(),
      this.createControls(),
      this.createMessage("Loading..."),
    );

    try {
      const docs = await getDocuments({ query: this.query, view: this.view });
      this.container.replaceChildren(this.createHeading(), this.createControls());

      if (docs.length === 0) {
        this.container.appendChild(this.createMessage("No documents found."));
        return;
      }

      const list = document.createElement("ul");
      list.className = "workflow-list";
      docs.forEach((doc) => list.appendChild(this.createDocumentItem(doc)));
      this.container.appendChild(list);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Could not load documents.";
      const status = this.createMessage(message);
      status.classList.add("gallery-status--error");
      this.container.replaceChildren(this.createHeading(), status);
    }
  }

  private createHeading(): HTMLHeadingElement {
    const heading = document.createElement("h3");
    heading.textContent = "Document Library";
    return heading;
  }

  private createControls(): HTMLFormElement {
    const form = document.createElement("form");
    form.className = "gallery-controls";
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = form.querySelector<HTMLInputElement>(".gallery-search");
      const select = form.querySelector<HTMLSelectElement>(".gallery-view");
      this.query = input?.value ?? "";
      this.view = (select?.value as DocumentView | undefined) ?? "active";
      void this.render();
    });

    const input = document.createElement("input");
    input.className = "gallery-search";
    input.type = "search";
    input.placeholder = "Search notes";
    input.setAttribute("aria-label", "Search notes");
    input.value = this.query;
    form.appendChild(input);

    const select = document.createElement("select");
    select.className = "gallery-view";
    select.setAttribute("aria-label", "Document view");
    for (const [value, label] of [
      ["active", "Active"],
      ["archived", "Archived"],
      ["trash", "Trash"],
    ] as const) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = value === this.view;
      select.appendChild(option);
    }
    form.appendChild(select);

    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "gallery-search-submit";
    submit.textContent = "Search";
    form.appendChild(submit);
    return form;
  }

  private createMessage(message: string): HTMLParagraphElement {
    const status = document.createElement("p");
    status.className = "gallery-status";
    status.textContent = message;
    return status;
  }

  private createDocumentItem(doc: DocumentResponse): HTMLLIElement {
    const item = document.createElement("li");
    item.className = "workflow-item";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "gallery-document";
    button.addEventListener("click", () => this.onSelectDocument(doc.document_id));

    const title = document.createElement("strong");
    title.className = "workflow-item-label";
    title.textContent = doc.title;
    button.appendChild(title);

    const metadata = document.createElement("span");
    metadata.className = "workflow-item-desc";
    const date = new Date(doc.updated_at ?? doc.created_at);
    const dateLabel = Number.isNaN(date.getTime()) ? "Unknown date" : date.toLocaleDateString();
    const pageCount = doc.pages?.length ?? doc.image_ids.length;
    const state = doc.trashed_at ? "Trash" : doc.archived_at ? "Archived" : "Active";
    metadata.textContent = `${state} · ${dateLabel} · ${pageCount} pages`;
    button.appendChild(metadata);

    item.appendChild(button);
    item.appendChild(this.createLifecycleActions(doc));
    return item;
  }

  private createLifecycleActions(doc: DocumentResponse): HTMLDivElement {
    const wrapper = document.createElement("div");
    wrapper.className = "gallery-actions";
    const action = document.createElement("button");
    action.type = "button";
    action.className = "gallery-action";
    action.textContent = doc.trashed_at
      ? "Restore"
      : doc.archived_at
        ? "Unarchive"
        : "Archive";
    action.addEventListener("click", async () => {
      action.disabled = true;
      try {
        if (doc.trashed_at) {
          await restoreDocument(doc.document_id);
        } else {
          await updateDocument(doc.document_id, { archived: !doc.archived_at });
        }
        await this.render();
      } catch (err: unknown) {
        action.disabled = false;
        const message = err instanceof Error ? err.message : "Could not update this document.";
        action.title = message;
      }
    });
    wrapper.appendChild(action);
    if (!doc.trashed_at) {
      const trash = document.createElement("button");
      trash.type = "button";
      trash.className = "gallery-action gallery-action--danger";
      trash.textContent = "Trash";
      trash.addEventListener("click", async () => {
        trash.disabled = true;
        try {
          await trashDocument(doc.document_id);
          await this.render();
        } catch (err: unknown) {
          trash.disabled = false;
          const message = err instanceof Error ? err.message : "Could not move this document to trash.";
          trash.title = message;
        }
      });
      wrapper.appendChild(trash);
    }
    return wrapper;
  }
}
