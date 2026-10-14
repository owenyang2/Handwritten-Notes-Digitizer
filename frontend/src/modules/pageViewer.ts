// Page raster viewer with manual complete-expression crop selection.

export interface PageViewerOptions {
  mount: HTMLElement;
  imageUrl: string;
  width: number;
  height: number;
  onSelect?: (bbox: {
    x: number;
    y: number;
    width: number;
    height: number;
  }) => void;
}

export class PageViewer {
  private readonly mount: HTMLElement;
  private stage!: HTMLElement;
  private overlays!: HTMLElement;
  private image?: HTMLImageElement;
  private start: { x: number; y: number } | null = null;

  constructor(options: PageViewerOptions) {
    this.mount = options.mount;
    this.render(options);
  }

  private render(options: PageViewerOptions): void {
    this.mount.replaceChildren();
    const canvas = document.createElement("canvas");
    canvas.setAttribute("data-testid", "page-viewer");
    canvas.width = options.width;
    canvas.height = options.height;
    // Keep large 200-DPI pages inside the responsive layout while retaining
    // the intrinsic pixel dimensions used for the crop coordinate space.
    canvas.style.display = "block";
    canvas.style.width = "100%";
    canvas.style.height = "auto";
    canvas.style.maxWidth = "100%";
    let ctx: CanvasRenderingContext2D | null = null;
    const hasCanvasImplementation =
      typeof navigator === "undefined" || !/jsdom/i.test(navigator.userAgent);
    try {
      // Some test/SSR DOMs expose canvas without installing a rendering
      // implementation. The viewer remains useful for selection there; a
      // real browser still supplies the 2-D context for the page image.
      if (hasCanvasImplementation) ctx = canvas.getContext("2d");
    } catch {
      ctx = null;
    }
    const image = new Image();
    this.image = image;
    // The API may serve page rasters from a separate origin and rely on the
    // authenticated browser session to authorize the request.
    image.crossOrigin = "use-credentials";
    image.onload = () => {
      ctx?.drawImage(image, 0, 0, options.width, options.height);
    };
    image.src = options.imageUrl;

    canvas.addEventListener("pointerdown", (event) => {
      const rect = canvas.getBoundingClientRect();
      this.start = {
        x: this.toImageCoordinate(
          event.clientX - rect.left,
          rect.width,
          options.width,
        ),
        y: this.toImageCoordinate(
          event.clientY - rect.top,
          rect.height,
          options.height,
        ),
      };
    });
    canvas.addEventListener("pointercancel", () => {
      this.start = null;
    });
    canvas.addEventListener("pointerup", (event) => {
      if (!this.start) return;
      const rect = canvas.getBoundingClientRect();
      const endX = this.toImageCoordinate(
        event.clientX - rect.left,
        rect.width,
        options.width,
      );
      const endY = this.toImageCoordinate(
        event.clientY - rect.top,
        rect.height,
        options.height,
      );
      const x = Math.min(this.start.x, endX);
      const y = Math.min(this.start.y, endY);
      const width = Math.abs(endX - this.start.x);
      const height = Math.abs(endY - this.start.y);
      this.start = null;
      if (width > 2 && height > 2) {
        options.onSelect?.({ x, y, width, height });
      }
    });
    this.stage = document.createElement("div");
    this.stage.className = "source-stage";
    this.overlays = document.createElement("div");
    this.overlays.className = "source-regions";
    this.stage.append(canvas, this.overlays);
    this.mount.appendChild(this.stage);
  }

  setZoom(percent: number): void {
    this.stage.style.width = `${percent}%`;
  }

  setRegions(
    regions: Array<{ region_id: string; bbox: number[] }>,
    selected: string | null,
    onSelect: (id: string) => void,
  ): void {
    const existing = new Map(
      [
        ...this.overlays.querySelectorAll<HTMLButtonElement>(
          "[data-region-id]",
        ),
      ].map((control) => [control.dataset.regionId, control]),
    );
    const current = new Set(regions.map((region) => region.region_id));
    for (const [id, control] of existing)
      if (!id || !current.has(id)) control.remove();
    const canvas = this.stage.querySelector("canvas")!;
    for (const [index, region] of regions.entries()) {
      const [x, y, width, height] = region.bbox;
      const control =
        existing.get(region.region_id) ?? document.createElement("button");
      control.type = "button";
      control.className = "source-region";
      control.dataset.regionId = region.region_id;
      control.setAttribute("aria-label", `Select original region ${index + 1}`);
      control.setAttribute(
        "aria-pressed",
        String(region.region_id === selected),
      );
      Object.assign(control.style, {
        left: `${(x / canvas.width) * 100}%`,
        top: `${(y / canvas.height) * 100}%`,
        width: `${(width / canvas.width) * 100}%`,
        height: `${(height / canvas.height) * 100}%`,
      });
      control.onclick = () => onSelect(region.region_id);
      if (!control.parentElement) this.overlays.append(control);
    }
  }

  dispose(): void {
    if (this.image) {
      this.image.onload = null;
      this.image.onerror = null;
    }
  }

  private toImageCoordinate(
    position: number,
    renderedSize: number,
    imageSize: number,
  ): number {
    if (renderedSize <= 0) return 0;
    return Math.max(
      0,
      Math.min(imageSize, Math.round((position / renderedSize) * imageSize)),
    );
  }
}
