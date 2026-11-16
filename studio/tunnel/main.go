// dctunnel gives the phone a fixed public HTTPS link with Tailscale Funnel and
// forwards it to the DriveCast panel on 127.0.0.1. It prints machine-readable
// lines for the Android app: STATE=..., PUBLIC_URL=..., ERROR=...
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"syscall"
	"time"

	"tailscale.com/tsnet"
)

func say(k, v string) { fmt.Printf("%s=%s\n", k, v); os.Stdout.Sync() }

func main() {
	dir := flag.String("dir", "", "state directory")
	host := flag.String("hostname", "drivecast", "tailnet machine name")
	target := flag.String("target", "http://127.0.0.1:8765", "local panel")
	flag.Parse()

	// Android has no /etc/resolv.conf; use public resolvers for the Go resolver.
	net.DefaultResolver = &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
		d := net.Dialer{Timeout: 5 * time.Second}
		var last error
		for _, s := range []string{"1.1.1.1:53", "8.8.8.8:53", "9.9.9.9:53"} {
			c, err := d.DialContext(ctx, network, s)
			if err == nil {
				return c, nil
			}
			last = err
		}
		return nil, errors.Join(errors.New("no DNS server reachable"), last)
	}}

	say("STATE", "starting")
	srv := &tsnet.Server{
		Hostname: *host,
		Dir:      *dir,
		AuthKey:  os.Getenv("TS_AUTHKEY"),
		Logf:     func(f string, a ...any) { fmt.Printf("TS: "+f+"\n", a...) },
		UserLogf: func(f string, a ...any) { fmt.Printf("TSU: "+f+"\n", a...) },
	}
	defer srv.Close()

	// Join the tailnet. With no auth key, print a sign-in link for the app to open.
	if err := srv.Start(); err != nil {
		say("ERROR", "Could not start Tailscale: "+err.Error())
		os.Exit(2)
	}
	lc, err := srv.LocalClient()
	if err != nil {
		say("ERROR", "Could not start Tailscale: "+err.Error())
		os.Exit(2)
	}
	deadline := time.Now().Add(20 * time.Minute)
	lastURL := ""
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		st, err := lc.StatusWithoutPeers(ctx)
		cancel()
		if err == nil {
			if st.BackendState == "Running" {
				break
			}
			if st.AuthURL != "" && st.AuthURL != lastURL {
				lastURL = st.AuthURL
				say("AUTH_URL", st.AuthURL)
				say("STATE", "needs_login")
			}
			if st.BackendState == "NeedsLogin" && st.AuthURL == "" && lastURL == "" {
				ctx2, cancel2 := context.WithTimeout(context.Background(), 20*time.Second)
				_ = lc.StartLoginInteractive(ctx2)
				cancel2()
			}
		}
		if time.Now().After(deadline) {
			say("ERROR", "Tailscale sign-in was not completed in 20 minutes. Tap Sign in again.")
			os.Exit(2)
		}
		time.Sleep(1500 * time.Millisecond)
	}
	say("STATE", "joined")

	ln, err := srv.ListenFunnel("tcp", ":443")
	if err != nil {
		say("ERROR", "Funnel is not allowed for this tailnet. In the Tailscale admin enable HTTPS and Funnel. ("+err.Error()+")")
		os.Exit(3)
	}
	domains := srv.CertDomains()
	if len(domains) == 0 {
		say("ERROR", "HTTPS certificates are not enabled in the Tailscale admin")
		os.Exit(4)
	}
	// Get the HTTPS certificate before announcing the link, so the browser never
	// sees a half-ready site. Retry until it works.
	for attempt := 1; ; attempt++ {
		cctx, ccancel := context.WithTimeout(context.Background(), 3*time.Minute)
		_, _, cerr := lc.CertPair(cctx, domains[0])
		ccancel()
		if cerr == nil {
			break
		}
		say("ERROR", "HTTPS certificate not ready yet (attempt "+fmt.Sprint(attempt)+"). In the Tailscale admin open DNS and press Enable HTTPS. Details: "+cerr.Error())
		time.Sleep(30 * time.Second)
	}
	say("PUBLIC_URL", "https://"+domains[0])
	say("STATE", "online")

	u, _ := url.Parse(*target)
	rp := httputil.NewSingleHostReverseProxy(u)
	base := rp.Director
	rp.Director = func(r *http.Request) {
		base(r)
		r.Header.Set("X-Forwarded-Proto", "https")
		if r.Header.Get("X-Forwarded-For") == "" {
			r.Header.Set("X-Forwarded-For", "funnel")
		}
	}
	rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		w.WriteHeader(http.StatusBadGateway)
		w.Write([]byte("DriveCast server is starting. Refresh in a few seconds."))
	}
	hs := &http.Server{Handler: rp, ReadHeaderTimeout: 20 * time.Second}

	go func() {
		ch := make(chan os.Signal, 1)
		signal.Notify(ch, syscall.SIGTERM, syscall.SIGINT)
		<-ch
		hs.Close()
	}()
	if err := hs.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
		say("ERROR", err.Error())
		os.Exit(5)
	}
}
