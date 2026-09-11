// Page raster viewer with manual complete-expression crop selection.

export interface PageViewerOptions {
  mount: HTMLElement;
  imageUrl: string;
  width: number;
  height: number;
  onSelect?: (bbox: { x: number; y: number; width: number; height: number }) => void;
}

export class PageViewer {
  private readonly mount: HTMLElement;
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
        x: this.toImageCoordinate(event.clientX - rect.left, rect.width, options.width),
        y: this.toImageCoordinate(event.clientY - rect.top, rect.height, options.height),
      };
    });
    canvas.addEventListener("pointerup", (event) => {
      if (!this.start) return;
      const rect = canvas.getBoundingClientRect();
      const endX = this.toImageCoordinate(event.clientX - rect.left, rect.width, options.width);
      const endY = this.toImageCoordinate(event.clientY - rect.top, rect.height, options.height);
      const x = Math.min(this.start.x, endX);
      const y = Math.min(this.start.y, endY);
      const width = Math.abs(endX - this.start.x);
      const height = Math.abs(endY - this.start.y);
      this.start = null;
      if (width > 2 && height > 2) {
        options.onSelect?.({ x, y, width, height });
      }
    });
    this.mount.appendChild(canvas);
  }

  private toImageCoordinate(position: number, renderedSize: number, imageSize: number): number {
    if (renderedSize <= 0) return 0;
    return Math.max(0, Math.min(imageSize, Math.round((position / renderedSize) * imageSize)));
  }
}
