/* The clip tray: finished clips kept in IndexedDB.
 *
 * Why not keep them in memory? Fifteen 1080p clips can be over 1 GB, which
 * gets an iPhone tab killed. IndexedDB stores Blobs on disk, so after each
 * clip is saved we drop the in-memory copy and keep only the disk-backed one.
 * It also means clips survive a reload, so a run can resume.
 * If storage is unavailable (private browsing), it falls back to memory.
 */
(function (root) {
  "use strict";
  const DB = "gaa-clips", STORE = "clips";

  function open() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "key" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function tx(db, mode, fn) {
    return new Promise((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const out = fn(t.objectStore(STORE));
      t.oncomplete = () => resolve(out && "result" in out ? out.result : undefined);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  }

  class Tray {
    constructor() { this.db = null; this.mem = new Map(); this.persistent = false; }

    async init() {
      try {
        this.db = await open();
        this.persistent = true;
        if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
      } catch (_) { this.db = null; }
    }

    /** rec: {key, kind: "reel" | "clip" | "piece", match, name, blob, ...}.
     *  Pieces are the parts of a reel still being made (kept for resuming). */
    async put(rec) {
      if (!this.db) { this.mem.set(rec.key, rec); return rec; }
      await tx(this.db, "readwrite", (s) => s.put(rec));
      // Re-read so we hold the disk-backed Blob, not the in-memory one.
      return (await this.get(rec.key)) || rec;
    }

    async get(key) {
      if (!this.db) return this.mem.get(key);
      return tx(this.db, "readonly", (s) => s.get(key));
    }

    async all() {
      const rows = this.db ? await tx(this.db, "readonly", (s) => s.getAll()) : [...this.mem.values()];
      return rows.sort((a, b) => a.key.localeCompare(b.key));
    }

    async has(key) { return !!(await this.get(key)); }

    async delete(key) {
      this.mem.delete(key);
      if (this.db) await tx(this.db, "readwrite", (s) => s.delete(key));
    }

    async clear() {
      this.mem.clear();
      if (this.db) await tx(this.db, "readwrite", (s) => s.clear());
    }
  }

  root.ClipTray = Tray;
})(self);
