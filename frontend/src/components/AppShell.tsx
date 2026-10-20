import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import {
  BrowserRouter,
  Link,
  useLocation,
  useNavigate,
  useMatch,
} from "react-router";
import * as Menu from "@radix-ui/react-dropdown-menu";
import {
  BookOpen,
  ArrowUpRight,
  ChevronRight,
  FileText,
  Grid2X2,
  Library,
  List,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings,
  Star,
  Upload,
} from "lucide-react";
import {
  getDocumentsPage,
  getPageRasterUrl,
  getProcessedImageUrl,
  restoreDocument,
  trashDocument,
  updateDocument,
  type DocumentResponse,
} from "../api/client.js";
import { AuthGate } from "../modules/authGate.js";
import {
  applyAppearance,
  readAppearance,
  saveAppearance,
  type Appearance,
} from "../appearance.js";
import { Button, Dialog, DialogContent } from "./ui/primitives.js";

export interface ShellMounts {
  upload: HTMLElement;
  batch: HTMLElement;
  editor: HTMLElement;
  math: HTMLElement;
  export: HTMLElement;
  calibration: HTMLElement;
  training: HTMLElement;
  sync: HTMLElement;
  privacy: HTMLElement;
  banner: HTMLElement;
}
export interface ShellHandle {
  refreshLibrary(): void;
  openDocument(id: string): void;
  setDocument(document: DocumentResponse): void;
  closeImport(): void;
  navigateTo(url: string, replace?: boolean): void;
  dispose(): void;
}
interface ShellProps {
  mounts: ShellMounts;
  username: string;
  onOpen(id: string, pageId?: string, regionId?: string): Promise<void>;
  onLogout(): void;
  onReauthenticated(): Promise<void>;
  onCloseImport(): void;
  connect(handle: Omit<ShellHandle, "dispose">): void;
}

// React owns the host; the existing editor exclusively owns its children.
export function LegacyHost({ node }: { node: HTMLElement }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    ref.current?.appendChild(node);
  }, [node]);
  return <div ref={ref} />;
}

function SignIn({ onAuthenticated }: { onAuthenticated(): void }) {
  const ref = useRef<HTMLDivElement>(null);
  const callback = useRef(onAuthenticated);
  callback.current = onAuthenticated;
  // Rendering the gate again would clear credentials the user is typing.
  useLayoutEffect(() => {
    if (ref.current)
      new AuthGate({
        mount: ref.current,
        onAuthenticated: () => callback.current(),
      }).render();
  }, []);
  return <div ref={ref} />;
}

function Shell({
  mounts,
  username,
  onOpen,
  onLogout,
  onReauthenticated,
  onCloseImport,
  connect,
}: ShellProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const pageMatch = useMatch("/notebooks/:documentId/pages/:pageId");
  const notebookMatch = useMatch("/notebooks/:documentId");
  const match = pageMatch ?? notebookMatch;
  const documentId = match?.params.documentId ?? null;
  const pageId = pageMatch?.params.pageId;
  const regionId =
    new URLSearchParams(location.search).get("region") ?? undefined;
  const settings = location.pathname === "/settings";
  const favorites = location.pathname === "/favorites";
  const preview = location.pathname === "/components";
  const libraryScroll = useRef(0);
  const libraryVisible = !documentId && !settings && !preview;
  useLayoutEffect(() => {
    // The Library restores its own position; native history restoration races it.
    const previous = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    return () => { window.history.scrollRestoration = previous; };
  }, []);
  useLayoutEffect(() => {
    const scrolling = document.scrollingElement ?? document.documentElement;
    if (!libraryVisible) {
      scrolling.scrollTop = 0;
      return;
    }
    scrolling.scrollTop = libraryScroll.current;
    const remember = () => { libraryScroll.current = scrolling.scrollTop; };
    window.addEventListener("scroll", remember, { passive: true });
    return () => window.removeEventListener("scroll", remember);
  }, [libraryVisible]);
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [current, setCurrent] = useState<DocumentResponse | null>(null);
  const [version, setVersion] = useState(0);
  const [query, setQuery] = useState("");
  const [layout, setLayout] = useState<"grid" | "list">("grid");
  const [view, setView] = useState<"active" | "archived" | "trash">("active");
  const [status, setStatus] = useState("Loading notebooks…");
  const [error, setError] = useState("");
  const importTrigger = useRef<HTMLElement | null>(null);
  const [importing, setImporting] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [compact, setCompact] = useState(() => window.innerWidth < 1024);
  const navigationTrigger = useRef<HTMLButtonElement | null>(null);
  const [navigationOpen, setNavigationOpen] = useState(
    () => window.innerWidth >= 1024,
  );
  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 1023px)");
    const update = () => {
      setCompact(media?.matches ?? false);
      setNavigationOpen(!media?.matches);
    };
    media?.addEventListener("change", update);
    return () => media?.removeEventListener("change", update);
  }, []);
  const [appearance, setAppearance] = useState<Appearance>(readAppearance);
  const libraryGeneration = useRef(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const openRef = useRef(onOpen);
  openRef.current = onOpen;

  useLayoutEffect(
    () =>
      connect({
        refreshLibrary: () => setVersion((value) => value + 1),
        openDocument: (id) => navigate(`/notebooks/${encodeURIComponent(id)}`),
        setDocument: setCurrent,
        closeImport: () => setImporting(false),
        navigateTo: (url, replace = false) => navigate(url, { replace }),
      }),
    [connect, navigate],
  );

  useEffect(() => {
    applyAppearance(appearance);
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const update = () => {
      if (appearance === "system") applyAppearance("system");
    };
    media?.addEventListener("change", update);
    return () => media?.removeEventListener("change", update);
  }, [appearance]);

  useEffect(() => {
    let active = true;
    ++libraryGeneration.current;
    setStatus("Loading notebooks…");
    setError("");
    void getDocumentsPage({ view: favorites ? "favorites" : view, query })
      .then((result) => {
        if (!active) return;
        setDocuments(result.items);
        setHasMore(result.hasMore);
        setStatus("");
      })
      .catch((err: unknown) => {
        if (!active) return;
        setStatus("");
        setError(
          err instanceof Error ? err.message : "Could not load notebooks.",
        );
      });
    return () => {
      active = false;
    };
  }, [view, version, query, favorites]);

  useEffect(() => {
    if (!documentId) return;
    setError("");
    void openRef.current(documentId, pageId, regionId).catch((err: unknown) => {
      setError(
        err instanceof Error ? err.message : "Could not open this notebook.",
      );
    });
  }, [documentId, pageId, regionId]);

  const mutate = async (operation: () => Promise<unknown>) => {
    try {
      await operation();
      setVersion((value) => value + 1);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not update this notebook.",
      );
    }
  };
  const openImport = (event: MouseEvent<HTMLButtonElement>) => {
    importTrigger.current = compact && navigationOpen
      ? navigationTrigger.current
      : event.currentTarget;
    if (compact) setNavigationOpen(false);
    setImporting(true);
  };
  const notebookLink = (doc: DocumentResponse) =>
    `/notebooks/${encodeURIComponent(doc.document_id)}`;
  const navigateCompact = () => {
    if (window.innerWidth < 1024) setNavigationOpen(false);
  };

  const sidebarContents = (
    <>
      <div className="sidebar-brand">
        <BookOpen size={24} strokeWidth={1.6} />
        <div className="brand-copy"><strong>Quiet Notebook</strong><span>Handwriting, made useful.</span></div>
        <Button
          variant="ghost"
          aria-label="Collapse navigation"
          onClick={() => setNavigationOpen(false)}
        >
          <PanelLeftClose size={18} />
        </Button>
      </div>
      <Button variant="secondary" className="sidebar-import" onClick={openImport}>
        <Plus size={17} /> New import
      </Button>
      <nav onClick={navigateCompact}>
        <Link
          className={
            !documentId && !settings && !favorites
              ? "nav-link active"
              : "nav-link"
          }
          to="/library"
          aria-current={libraryVisible && !favorites ? "page" : undefined}
        >
          <Library size={18} />
          Library
        </Link>
        <Link
          className={favorites ? "nav-link active" : "nav-link"}
          to="/favorites"
          aria-current={favorites ? "page" : undefined}
        >
          <Star size={18} />
          Favorites
        </Link>
      </nav>
      <p className="nav-caption">Recent notebooks</p>
      <nav className="recent-notebooks" onClick={navigateCompact}>
        {documents
          .filter((doc) => !doc.trashed_at && !doc.archived_at)
          .slice(0, 7)
          .map((doc) => (
            <Link
              key={doc.document_id}
              to={notebookLink(doc)}
              className={`nav-link ${documentId === doc.document_id ? "active" : ""}`}
            >
              <FileText size={16} />
              <span>{doc.title}</span>
            </Link>
          ))}
      </nav>
      <div className="sidebar-bottom">
        <p className="sidebar-note">From the first rough idea<br />to notes you can use.</p>
        <Link
          to="/settings"
          className={`nav-link ${settings ? "active" : ""}`}
          onClick={navigateCompact}
        >
          <Settings size={18} />
          Settings
        </Link>
        <div className="account-bar" data-testid="account-bar">
          <span className="account-avatar" aria-hidden="true">{(username || "Q").slice(0, 1).toUpperCase()}</span>
          <span className="account-user">{username || "Your workspace"}</span>
          <Button
            variant="ghost"
            data-testid="account-logout"
            onClick={onLogout}
          >
            Log out
          </Button>
        </div>
      </div>
    </>
  );
  return (
    <div
      className="notebook-app"
      data-navigation={navigationOpen ? "open" : "closed"}
    >
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <aside
        className="notebook-sidebar"
        aria-label="Notebook navigation"
        hidden={!navigationOpen || compact}
      >
        {sidebarContents}
      </aside>
      <Dialog open={navigationOpen && compact} onOpenChange={setNavigationOpen}>
        <DialogContent
          title="Your workspace"
          description="Library, recent notebooks, and settings."
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            navigationTrigger.current?.focus();
          }}
        >
          <div className="mobile-navigation">{sidebarContents}</div>
        </DialogContent>
      </Dialog>
      <div className="notebook-main">
        <header className="notebook-topbar">
          <Button
            ref={navigationTrigger}
            variant="ghost"
            aria-label={navigationOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={navigationOpen}
            onClick={() => setNavigationOpen(!navigationOpen)}
          >
            <PanelLeftOpen size={18} />
          </Button>
          <Link to="/library">{settings ? "Settings" : favorites ? "Favorites" : "Library"}</Link>
          {documentId && (
            <>
              <ChevronRight size={14} />
              <span className="breadcrumb-title">
                {current?.document_id === documentId
                  ? current.title
                  : "Opening notebook…"}
              </span>
            </>
          )}
          <div className="topbar-actions">
            {documentId ? (
              <Button
                variant="secondary"
                onClick={() => {
                  const button = mounts.math.querySelector<HTMLButtonElement>(
                    "[data-testid=notebook-export-open]",
                  );
                  if (mounts.math.style.display !== "none" && button)
                    button.click();
                  else setExporting(true);
                }}
              >
                Export
              </Button>
            ) : (
              <Button onClick={openImport}>
                <Plus size={16} />
                Import notes
              </Button>
            )}
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="notebook-content">
          <LegacyHost node={mounts.banner} />
          {error && (
            <div className="persistent-error" role="alert">
              {error}{" "}
              <Button
                variant="secondary"
                onClick={() => {
                  setVersion((value) => value + 1);
                  if (documentId)
                    void onOpen(documentId, pageId, regionId).catch(
                      (err: unknown) => setError(String(err)),
                    );
                }}
              >
                Retry
              </Button>
            </div>
          )}
          <section
            hidden={!!documentId || settings || preview}
            data-testid="document-history"
            className="library-screen"
          >
            <div className="screen-heading">
              <div>
                <h1>{favorites ? "Favorites" : "Library"}</h1>
                <p className="screen-description">{favorites ? "The notebooks you want to keep close." : "Your handwriting. A little more possibility."}</p>
              </div>
              <span className="workspace-label"><BookOpen size={16} /> Personal workspace</span>
            </div>
            <div className="library-controls">
              <label className="search-field">
                <Search size={18} />
                <input
                  type="search"
                  aria-label="Search notes"
                  placeholder="Search notebooks…"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <div className="library-view-controls">
              <select
                aria-label="Document view"
                value={view}
                onChange={(event) => setView(event.target.value as typeof view)}
              >
                <option value="active">All notebooks</option>
                <option value="archived">Archived</option>
                <option value="trash">Trash</option>
              </select>
              <div className="view-switch" role="group" aria-label="Library layout">
                <Button variant="ghost" aria-label="Grid view" aria-pressed={layout === "grid"} onClick={() => setLayout("grid")}><Grid2X2 size={17} /></Button>
                <Button variant="ghost" aria-label="List view" aria-pressed={layout === "list"} onClick={() => setLayout("list")}><List size={18} /></Button>
              </div>
              </div>
            </div>
            <div className="collection-heading">
              <h2>{query ? "Search results" : favorites ? "Saved favorites" : view === "archived" ? "Archived notebooks" : view === "trash" ? "Trash" : "Your notebooks"}</h2>
              <p role="status" className="collection-count">
              {status || (error ? "" : `${documents.length}${hasMore ? "+" : ""} ${documents.length === 1 && !hasMore ? "notebook" : "notebooks"}`)}
              </p>
            </div>
            {status && !documents.length && <div className="library-skeleton" aria-hidden="true"><span /><span /><span /></div>}
            {!status && !error && !documents.length && (
              <div className="library-empty">
                <div className="empty-mark" aria-hidden="true"><BookOpen size={40} strokeWidth={1.2} /></div>
                <h2>
                  {query
                    ? "No matching notebooks"
                    : favorites
                      ? "Keep your favorites close"
                      : view === "active"
                        ? "Your next idea starts here"
                        : `No notebooks in ${view}`}
                </h2>
                <p>
                  {query
                    ? "Try a different title."
                    : favorites
                      ? "Use a notebook’s menu to add it to Favorites."
                      : "Import handwritten pages to keep the original beside text you can correct and export."}
                </p>
                {view === "active" && !favorites && (
                  !query &&
                  <Button onClick={openImport}>
                    <Upload size={16} />
                    Import notes
                  </Button>
                )}
                {!query && !favorites && view === "active" && <p className="empty-formats">PDF, PNG or JPEG · Originals stay alongside your corrections</p>}
              </div>
            )}
            <ul className="notebook-grid" data-layout={layout} aria-label="Notebooks">
              {documents.map((doc) => {
                const firstPage = doc.pages?.[0];
                const image = firstPage
                  ? getPageRasterUrl(firstPage.page_id)
                  : doc.image_ids[0]
                    ? getProcessedImageUrl(doc.image_ids[0])
                    : null;
                const date = new Date(doc.updated_at ?? doc.created_at);
                return (
                  <li className="notebook-card" key={doc.document_id}>
                    <Link className="gallery-document" to={notebookLink(doc)}>
                      <div className="notebook-thumbnail">
                        {image ? (
                          <img src={image} alt="" loading="lazy" />
                        ) : (
                          <FileText size={36} strokeWidth={1} />
                        )}
                      </div>
                      <div className="notebook-card-copy">
                        <h2>{doc.title}</h2>
                        <p>
                          {doc.pages?.length ?? doc.image_ids.length} pages{" "}
                          <span aria-hidden="true">·</span>{" "}
                          {Number.isNaN(date.getTime())
                            ? ""
                            : date.toLocaleDateString(undefined, {
                                month: "short",
                                day: "numeric",
                              })}
                        </p>
                      </div>
                      <ArrowUpRight className="notebook-open" size={18} aria-hidden="true" />
                    </Link>
                    <Menu.Root modal={false}>
                      <Menu.Trigger asChild>
                        <Button
                          variant="ghost"
                          className="notebook-menu"
                          aria-label={`Actions for ${doc.title}`}
                        >
                          <MoreHorizontal size={18} />
                        </Button>
                      </Menu.Trigger>
                      <Menu.Portal>
                        <Menu.Content
                          className="dropdown-content"
                          align="end"
                          sideOffset={4}
                        >
                          <Menu.Item
                            className="dropdown-item"
                            onSelect={() =>
                              void mutate(() =>
                                updateDocument(doc.document_id, {
                                  is_favorite: !doc.is_favorite,
                                  expected_sync_version: doc.sync_version,
                                }),
                              )
                            }
                          >
                            {doc.is_favorite
                              ? "Remove favorite"
                              : "Add to Favorites"}
                          </Menu.Item>
                          {!doc.trashed_at && (
                            <Menu.Item
                              className="dropdown-item"
                              onSelect={() => {
                                const title = window.prompt(
                                  "Notebook title",
                                  doc.title,
                                );
                                if (title?.trim())
                                  void mutate(() =>
                                    updateDocument(doc.document_id, {
                                      title: title.trim(),
                                      expected_sync_version: doc.sync_version,
                                    }),
                                  );
                              }}
                            >
                              Rename
                            </Menu.Item>
                          )}
                          <Menu.Item
                            className="dropdown-item"
                            onSelect={() =>
                              void mutate(() =>
                                doc.trashed_at
                                  ? restoreDocument(doc.document_id)
                                  : updateDocument(doc.document_id, {
                                      archived: !doc.archived_at,
                                      expected_sync_version: doc.sync_version,
                                    }),
                              )
                            }
                          >
                            {doc.trashed_at
                              ? "Restore"
                              : doc.archived_at
                                ? "Unarchive"
                                : "Archive"}
                          </Menu.Item>
                          {!doc.trashed_at && (
                            <Menu.Item
                              className="dropdown-item danger"
                              onSelect={() =>
                                void mutate(() =>
                                  trashDocument(doc.document_id),
                                )
                              }
                            >
                              Move to Trash
                            </Menu.Item>
                          )}
                        </Menu.Content>
                      </Menu.Portal>
                    </Menu.Root>
                    {doc.is_favorite && (
                      <Star
                        className="favorite-marker"
                        size={14}
                        aria-label="Favorite"
                      />
                    )}
                  </li>
                );
              })}
            </ul>
            {!status && documents.length > 0 && <p className="library-footnote">Open a notebook to compare the original, refine the text, and export.</p>}
            {hasMore && (
              <Button
                variant="secondary"
                disabled={loadingMore}
                onClick={async () => {
                  const generation = libraryGeneration.current;
                  setLoadingMore(true);
                  try {
                    const result = await getDocumentsPage({
                      view: favorites ? "favorites" : view,
                      query,
                      offset: documents.length,
                    });
                    if (generation !== libraryGeneration.current) return;
                    setDocuments((current) => [...current, ...result.items]);
                    setHasMore(result.hasMore);
                  } catch (err) {
                    setError(
                      err instanceof Error
                        ? err.message
                        : "Could not load more notebooks.",
                    );
                  } finally {
                    setLoadingMore(false);
                  }
                }}
              >
                {loadingMore ? "Loading…" : "Load more notebooks"}
              </Button>
            )}
          </section>
          <section
            hidden={!documentId}
            className="document-screen"
            aria-label="Notebook workspace"
          >
            <div className="document-heading">
            <h1>
              {current?.document_id === documentId
                ? current.title
                : "Opening notebook…"}
            </h1>
            </div>
            <LegacyHost node={mounts.math} />
            <LegacyHost node={mounts.editor} />
          </section>
          <section hidden={!settings} className="settings-screen">
            <div className="screen-heading"><h1>Settings</h1><p className="screen-description">Make room for the way you work.</p></div>
            <section className="settings-group">
              <div className="settings-intro">
              <h2>Account</h2>
              <p className="muted">
                If your session expires, sign in again here to keep your current
                review open.
              </p>
              </div>
              <div className="settings-panel">
              <p className="settings-username">{username}</p>
              <Button variant="secondary" onClick={() => setSigningIn(true)}>
                Sign in again
              </Button>
              </div>
            </section>
            <section className="settings-group">
              <div className="settings-intro">
              <h2>Appearance</h2>
              <p className="muted">
                Choose how your workspace looks. Original pages keep their
                colors.
              </p>
              </div>
              <div className="settings-panel">
              <label>
                Appearance{" "}
                <select
                  aria-label="Appearance"
                  value={appearance}
                  onChange={(event) => {
                    const value = event.target.value as Appearance;
                    saveAppearance(value);
                    setAppearance(value);
                  }}
                >
                  <option value="system">System</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </select>
              </label>
              <div className="appearance-preview" aria-hidden="true"><span /><div><span /><span /><span /></div></div>
              </div>
            </section>
            <section className="settings-group">
              <LegacyHost node={mounts.sync} />
            </section>
            <details className="settings-group">
              <summary>Privacy and optional personalization</summary>
              <LegacyHost node={mounts.privacy} />
              <LegacyHost node={mounts.training} />
              <LegacyHost node={mounts.calibration} />
            </details>
            <details className="settings-group">
              <summary>Advanced</summary>
              <p>Review shared controls and their keyboard states.</p>
              <Link to="/components">Component preview</Link>
            </details>
          </section>
          <section hidden={!preview} className="component-preview">
            <h1>Component preview</h1>
            <p>Use Settings to compare Light, Dark, and System appearance.</p>
            <div className="flex flex-wrap gap-4">
              <Button>Save review</Button>
              <Button variant="secondary">Export last saved version</Button>
              <Button variant="ghost">Cancel</Button>
              <Button disabled>Saving…</Button>
            </div>
            <label>
              Long notebook title{" "}
              <input defaultValue="Research notes — a realistic title that wraps on small screens" />
            </label>
            <div className="persistent-error">
              Source changed — convert again. Your correction is still here.
            </div>
          </section>
          <div key={`import-${importing}`} hidden>
            <LegacyHost node={mounts.upload} />
            <LegacyHost node={mounts.batch} />
          </div>
          <div key={`export-${exporting}`} hidden>
            <LegacyHost node={mounts.export} />
          </div>
        </main>
      </div>
      <Dialog open={signingIn} onOpenChange={setSigningIn}>
        <DialogContent
          title="Sign in again"
          description="Your current notebook and corrections stay open."
        >
          <SignIn
            onAuthenticated={() => {
              setSigningIn(false);
              void onReauthenticated()
                .then(() => setVersion((value) => value + 1))
                .catch((err: unknown) =>
                  setError(
                    err instanceof Error
                      ? err.message
                      : "Could not confirm your account.",
                  ),
                );
            }}
          />
        </DialogContent>
      </Dialog>
      <Dialog
        open={importing}
        onOpenChange={(open) => {
          setImporting(open);
          if (!open) onCloseImport();
        }}
      >
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            importTrigger.current?.focus();
          }}
          title="Import notes"
          description="Choose an image or PDF, or add several files to a recoverable batch. Imported notes appear in your Library."
        >
          <LegacyHost node={mounts.upload} />
          <details>
            <summary>Import multiple files</summary>
            <LegacyHost node={mounts.batch} />
          </details>
        </DialogContent>
      </Dialog>
      <Dialog open={exporting} onOpenChange={setExporting}>
        <DialogContent
          title="Export notebook"
          description="Choose a format below. Excluded regions stay out of your export."
        >
          <LegacyHost node={mounts.export} />
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function mountShell(
  root: HTMLElement,
  props: Omit<ShellProps, "connect">,
): ShellHandle {
  const reactRoot = createRoot(root);
  let handle!: Omit<ShellHandle, "dispose">;
  flushSync(() =>
    reactRoot.render(
      <BrowserRouter>
        <Shell
          {...props}
          connect={(value) => {
            handle = value;
          }}
        />
      </BrowserRouter>,
    ),
  );
  return {
    refreshLibrary: () => handle.refreshLibrary(),
    openDocument: (id) => handle.openDocument(id),
    navigateTo: (url, replace) => handle.navigateTo(url, replace),
    setDocument: (doc) => handle.setDocument(doc),
    closeImport: () => handle.closeImport(),
    dispose: () => reactRoot.unmount(),
  };
}
