// Review queue of unresolved math/prose regions.

export interface ReviewItem {
  id: string;
  kind: "math" | "text" | "figure";
  preview: string;
  unresolved: boolean;
}

export class ReviewQueue {
  constructor(private readonly mount: HTMLElement) {}

  render(items: ReviewItem[], onSelect?: (id: string) => void): void {
    this.mount.replaceChildren();
    const list = document.createElement("ol");
    list.className = "review-queue";
    list.setAttribute("data-testid", "review-queue");
    for (const item of items) {
      const li = document.createElement("li");
      if (item.unresolved) li.dataset.unresolved = "true";
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = item.preview;
      button.setAttribute("data-region-id", item.id);
      button.setAttribute("aria-label", `Review ${item.kind} region: ${item.preview}`);
      button.addEventListener("click", () => onSelect?.(item.id));
      li.appendChild(button);
      list.appendChild(li);
    }
    this.mount.appendChild(list);
  }
}
