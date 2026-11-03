/**
 * IndexedDB storage for uploaded videos.
 * Blob URLs don't survive page navigation/refresh — IndexedDB does.
 *
 * Videos are keyed by analysis ID so revisiting a past analysis from the
 * dashboard retrieves the correct video, not the most recent upload.
 * A `latest_upload` key is also written as a fallback for the immediate
 * post-analysis navigation, before we know the analysis ID on disk.
 */

const DB_NAME = 'prysm_videos'
const STORE_NAME = 'videos'
const LATEST_KEY = 'latest_upload'
const MAX_PER_USER = 20

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE_NAME)
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function videoKey(analysisId: string): string {
  return `video_${analysisId}`
}

/** Save a video File to IndexedDB, keyed by analysis ID (plus a `latest_upload` pointer). */
export async function saveVideo(file: File, analysisId?: string): Promise<void> {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    store.put(file, LATEST_KEY)
    if (analysisId) store.put(file, videoKey(analysisId))
    tx.oncomplete = () => {
      resolve()
      pruneOldVideos().catch(() => {})
    }
    tx.onerror = () => reject(tx.error)
  })
}

/**
 * Retrieve the saved video as a blob URL.
 * - If `analysisId` is given, looks up that specific video only — no fallback to
 *   `latest_upload`. This prevents a cross-device situation (e.g. analysed on mobile,
 *   viewed on desktop) from showing the wrong video.
 * - If no `analysisId`, returns the `latest_upload` pointer (used right after submit
 *   before the analysis ID is known).
 */
export async function getVideoUrl(analysisId?: string): Promise<string | null> {
  try {
    const db = await openDB()
    return new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, 'readonly')
      const store = tx.objectStore(STORE_NAME)

      const tryKey = (key: string, onMiss: () => void) => {
        const req = store.get(key)
        req.onsuccess = () => {
          if (req.result) resolve(URL.createObjectURL(req.result))
          else onMiss()
        }
        req.onerror = () => onMiss()
      }

      if (analysisId) {
        // When a specific analysis is requested, only return its video — no cross-video fallback.
        tryKey(videoKey(analysisId), () => resolve(null))
      } else {
        tryKey(LATEST_KEY, () => resolve(null))
      }
    })
  } catch {
    return null
  }
}

/** Clear the latest upload pointer. Per-analysis copies remain for revisit. */
export async function clearVideo(): Promise<void> {
  try {
    const db = await openDB()
    const tx = db.transaction(STORE_NAME, 'readwrite')
    tx.objectStore(STORE_NAME).delete(LATEST_KEY)
  } catch {
    // silently fail
  }
}

/** Keep IndexedDB from growing unbounded — drop the oldest per-analysis videos. */
async function pruneOldVideos(): Promise<void> {
  const db = await openDB()
  const keys: string[] = await new Promise((resolve) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const req = tx.objectStore(STORE_NAME).getAllKeys()
    req.onsuccess = () => resolve((req.result as IDBValidKey[]).map(String))
    req.onerror = () => resolve([])
  })
  const perAnalysis = keys.filter(k => k.startsWith('video_'))
  if (perAnalysis.length <= MAX_PER_USER) return
  const toDrop = perAnalysis.slice(0, perAnalysis.length - MAX_PER_USER)
  const tx = db.transaction(STORE_NAME, 'readwrite')
  const store = tx.objectStore(STORE_NAME)
  toDrop.forEach(k => store.delete(k))
}
