import { getDocuments } from "../api/client.js";

export class GalleryUI {
  private container: HTMLElement;
  private onSelectDocument: (docId: string) => void;

  constructor(containerId: string, onSelectDocument: (docId: string) => void) {
    const el = document.getElementById(containerId);
    if (!el) throw new Error(`Container #${containerId} not found`);
    this.container = el;
    this.onSelectDocument = onSelectDocument;
  }

  public async render() {
    this.container.innerHTML = "<h3>Document History</h3><p>Loading...</p>";
    
    try {
      const docs = await getDocuments();
      
      this.container.innerHTML = "<h3>Document History</h3>";
      const list = document.createElement("ul");
      list.className = "workflow-list";
      
      if (docs.length === 0) {
        this.container.innerHTML += "<p>No documents found.</p>";
        return;
      }
      
      docs.forEach((doc: any) => {
        const li = document.createElement("li");
        li.className = "workflow-item";
        li.style.cursor = "pointer";
        li.innerHTML = `
          <strong class="workflow-item-label">${doc.title}</strong>
          <span class="workflow-item-desc">${new Date(doc.created_at).toLocaleDateString()} - ${doc.image_ids.length} pages</span>
        `;
        li.addEventListener("click", () => {
          this.onSelectDocument(doc.document_id);
        });
        list.appendChild(li);
      });
      
      this.container.appendChild(list);
    } catch (err: any) {
      this.container.innerHTML = `<h3>Document History</h3><p style="color: red">${err.message}</p>`;
    }
  }
}
