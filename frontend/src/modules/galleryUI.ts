import { getDocuments } from "../api/client.js";
import type { DocumentResponse } from "../api/client.js";

export class GalleryUI {
  private container: HTMLElement;
  private onSelectDocument: (docId: string) => void;

  constructor(containerId: string, onSelectDocument: (docId: string) => void) {
    const el = document.getElementById(containerId);
    if (!el) throw new Error(`Container #${containerId} not found`);
    this.container = el;
    this.onSelectDocument = onSelectDocument;
  }

  public async render(): Promise<void> {
    this.container.replaceChildren(this.createHeading(), this.createMessage("Loading..."));

    try {
      const docs = await getDocuments();
      this.container.replaceChildren(this.createHeading());

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
    heading.textContent = "Document History";
    return heading;
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
    const date = new Date(doc.created_at);
    const dateLabel = Number.isNaN(date.getTime()) ? "Unknown date" : date.toLocaleDateString();
    metadata.textContent = `${dateLabel} - ${doc.image_ids.length} pages`;
    button.appendChild(metadata);

    item.appendChild(button);
    return item;
  }
}
