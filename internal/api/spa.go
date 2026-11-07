package api

import (
	"io/fs"
	"net/http"
	"path"
	"strconv"
	"strings"
)

const (
	cacheImmutable = "public, max-age=31536000, immutable"
	cacheNone      = "no-cache"
)

// spa serves the embedded build. A path with no file is index.html, so a refresh of
// any frontend route still loads the app. index.html is not cached; files under
// _app/immutable/ are.
func spa(fsys fs.FS) http.Handler {
	files := http.FileServer(http.FS(fsys))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.Header().Set("Allow", "GET, HEAD")
			writeProblem(w, http.StatusMethodNotAllowed, "method_not_allowed", "Method Not Allowed", "")
			return
		}
		name := strings.TrimPrefix(path.Clean("/"+r.URL.Path), "/")
		if name == "" || name == "." || name == "index.html" || !isFile(fsys, name) {
			serveIndex(w, r, fsys)
			return
		}
		if strings.HasPrefix(name, "_app/immutable/") {
			w.Header().Set("Cache-Control", cacheImmutable)
		}
		files.ServeHTTP(w, r)
	})
}

func isFile(fsys fs.FS, name string) bool {
	f, err := fsys.Open(name)
	if err != nil {
		return false
	}
	defer f.Close()
	st, err := f.Stat()
	return err == nil && !st.IsDir()
}

func serveIndex(w http.ResponseWriter, r *http.Request, fsys fs.FS) {
	b, err := fs.ReadFile(fsys, "index.html")
	if err != nil {
		writeProblem(w, http.StatusInternalServerError, "internal", "Internal Server Error", "")
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", cacheNone)
	w.Header().Set("Content-Length", strconv.Itoa(len(b)))
	w.WriteHeader(http.StatusOK)
	if r.Method != http.MethodHead {
		_, _ = w.Write(b)
	}
}
