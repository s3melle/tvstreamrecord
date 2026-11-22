#!/usr/bin/env python3
"""Enregistre un flux (ffmpeg -c copy) en choisissant tout seul les pistes.

Deux façons de l'appeler :

  1. A la main
       rec.py URL SORTIE.ts [-t SECONDES] [-m HAUTEUR_MAX] [--no-subs] [--dry]

  2. A la place de ffmpeg dans tvstreamrecord (page Configuration)
       Chemin ffmpeg      : /opt/tvstreamrecord/rec.py     (après chmod +x rec.py)
       Paramètres ffmpeg  : -m 720 -metadata title=%fulltitle%
     tvstreamrecord lance alors :  rec.py -i URL -y -t DUREE <paramètres> FICHIER
     Les placeholders de tvstreamrecord (%fulltitle% = nom de l'enregistrement, %title%,
     %channel%, %date%...) sont déjà remplacés quand rec.py reçoit les arguments.

Options de rec.py : -m HAUTEUR_MAX, --no-subs (ne pas mapper les sous-titres), --dry,
--raw (impose le mode brut), --ffmpeg (impose ffmpeg, sans mode brut).
Tout autre argument (ex. -metadata title=...) est transmis tel quel à ffmpeg, avant le
fichier de sortie. Les anciens -map ... , -c ... et -loglevel ... sont ignorés : rec.py gère.

Deux modes d'enregistrement :
  * MODE BRUT (automatique pour une playlist HLS « simple »: audio et vidéo dans les mêmes
    segments TS, sans chiffrement). rec.py télécharge lui-même les segments et les écrit
    bout à bout. ffmpeg ne sait pas suivre un changement de flux en cours d'enregistrement
    (nouveau PID vidéo après un changement de clip: tout ce qui suit est jeté et on n'a plus
    que le son, constaté sur Pluto le 22/11); en mode brut rien n'est démultiplexé, donc rien
    ne se perd. Un segment en erreur (502...) est redemandé plusieurs fois. Les arguments
    destinés à ffmpeg (-metadata...) sont ignorés dans ce mode.
  * MODE FFMPEG (repli): audio/sous-titres dans des playlists séparées, segments chiffrés
    (AES), fMP4, ou source qui n'est pas du HLS. Choix des pistes avec ffprobe, voir plus bas.

Vidéo      : la plus haute qualité qui ne dépasse pas HAUTEUR_MAX (720 -> le 1280x720).
Audio      : une piste par langue/titre (la meilleure débit), donc toutes les langues,
             sans les doublons « même son à plusieurs débits ».
Sous-titres: -map 0:s? (tous ceux qui existent, aucune erreur s'il n'y en a pas).

Une fois les pistes choisies, rec.py se remplace par ffmpeg (exec) : tvstreamrecord
arrête donc bien ffmpeg lui-même (terminate) et ffmpeg écrit dans SORTIE.log.
"""
import json, os, re, signal, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request

T0 = time.time()


def usage(msg=None):
    if msg:
        sys.stderr.write("rec.py: %s\n" % msg)
    sys.stderr.write(__doc__)
    sys.exit(2)


def valeur(argv, i):
    if i + 1 >= len(argv):
        usage("valeur manquante après %s" % argv[i])
    return argv[i + 1]


def entier(txt, nom):
    try:
        return int(float(txt))
    except ValueError:
        usage("%s attend un nombre, reçu %r" % (nom, txt))


entree, duree, hmax, dry, nosubs = None, 0, 100000, False, False
forcer = None                    # "brut", "ffmpeg" ou None (automatique)
reste = []
argv = sys.argv[1:]
i = 0
while i < len(argv):
    x = argv[i]
    if x in ("-h", "--help"):
        usage()
    elif x == "-i":
        entree = valeur(argv, i); i += 2
    elif x == "-t":
        duree = entier(valeur(argv, i), "-t"); i += 2
    elif x == "-m":
        hmax = entier(valeur(argv, i), "-m"); i += 2
    elif x == "-y":
        i += 1
    elif x in ("-loglevel", "-v", "-map", "-c"):   # gérés par rec.py: on ignore (et leur valeur)
        valeur(argv, i); i += 2
    elif x == "--dry":
        dry = True; i += 1
    elif x == "--no-subs":
        nosubs = True; i += 1
    elif x == "--raw":
        forcer = "brut"; i += 1
    elif x == "--ffmpeg":
        forcer = "ffmpeg"; i += 1
    else:
        reste.append(x); i += 1

if entree:                       # syntaxe ffmpeg: le dernier argument est le fichier de sortie
    if not reste:
        usage("fichier de sortie manquant")
    url, sortie, extra = entree, reste[-1], reste[:-1]
else:                            # URL SORTIE
    if len(reste) < 2:
        usage("il faut une URL et un fichier de sortie")
    url, sortie, extra = reste[0], reste[1], reste[2:]

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

# ======================================================================================
# MODE BRUT: copie des segments TS d'une playlist HLS, sans passer par ffmpeg
# ======================================================================================
UA = "Mozilla/5.0 (rec.py)"
NON_TS = (".m4s", ".mp4", ".m4a", ".m4v", ".cmfv", ".cmfa", ".aac", ".ac3", ".mp3", ".vtt", ".webvtt")


def http(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=timeout)


def lire_playlist(url, timeout=20):
    """(url finale, texte) d'une playlist; ValueError si ce n'est pas du HLS (flux infini...)."""
    with http(url, timeout) as r:
        debut = r.read(16)
        if not debut.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"#EXTM3U"):
            raise ValueError("pas une playlist HLS")
        return r.geturl() or url, (debut + r.read(2000000)).decode("utf-8", "replace")


def attributs(ligne):
    liste = ligne.split(":", 1)[1] if ":" in ligne else ""
    return {k: v.strip('"') for k, v in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', liste)}


def incompatible_brut(texte, base):
    """Raison pour laquelle cette playlist MEDIA ne peut pas être copiée telle quelle, sinon ''."""
    premier = None
    for ligne in texte.splitlines():
        l = ligne.strip()
        if l.startswith("#EXT-X-KEY:") and attributs(l).get("METHOD", "NONE").upper() != "NONE":
            return "segments chiffrés (AES)"
        if l.startswith("#EXT-X-MAP"):
            return "segments fMP4"
        if l.startswith("#EXT-X-BYTERANGE"):
            return "segments en BYTERANGE"
        if l and not l.startswith("#") and premier is None:
            premier = l
    if premier is None:
        return "aucun segment dans la playlist"
    chemin = urllib.parse.urlsplit(urllib.parse.urljoin(base, premier)).path.lower()
    if chemin.endswith(NON_TS):
        return "segments qui ne sont pas du TS"
    return ""


def plan_brut(url, hmax, nosubs):
    """(url de la playlist media, description) ou (None, raison du repli sur ffmpeg)."""
    if not url.lower().startswith(("http://", "https://")):
        return None, "source qui n'est pas en HTTP"
    try:
        final, texte = lire_playlist(url)
    except Exception as e:
        return None, "playlist illisible ou non HLS (%s)" % e
    lignes = [l.strip() for l in texte.splitlines() if l.strip()]
    desc = "playlist media"
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lignes):
        sep = set()                     # groupes audio/sous-titres qui ont leurs propres fichiers
        for l in lignes:
            if l.startswith("#EXT-X-MEDIA:"):
                a = attributs(l)
                if a.get("URI") and a.get("TYPE", "").upper() in ("AUDIO", "SUBTITLES"):
                    sep.add((a["TYPE"].upper(), a.get("GROUP-ID", "")))
        variantes = []
        for n, l in enumerate(lignes):
            if l.startswith("#EXT-X-STREAM-INF"):
                a = attributs(l)
                uri = next((x for x in lignes[n + 1:] if not x.startswith("#")), None)
                if not uri:
                    continue
                m = re.match(r"(\d+)x(\d+)", a.get("RESOLUTION", ""))
                variantes.append({
                    "uri": urllib.parse.urljoin(final, uri),
                    "h": int(m.group(2)) if m else 0, "w": int(m.group(1)) if m else 0,
                    "bw": int(a.get("BANDWIDTH", "0") or 0),
                    "audio": a.get("AUDIO"), "subs": a.get("SUBTITLES")})
        if not variantes:
            return None, "master sans variante exploitable"
        ok = [v for v in variantes if v["h"] <= hmax] or [min(variantes, key=lambda v: v["h"])]
        v = max(ok, key=lambda v: (v["h"], v["bw"]))
        if v["audio"] and ("AUDIO", v["audio"]) in sep:
            return None, "audio dans une playlist séparée"
        if v["subs"] and not nosubs and ("SUBTITLES", v["subs"]) in sep:
            return None, "sous-titres dans une playlist séparée"
        try:
            final, texte = lire_playlist(v["uri"])
        except Exception as e:
            return None, "playlist de la variante illisible (%s)" % e
        desc = "variante %s, %d bit/s" % ("%dx%d" % (v["w"], v["h"]) if v["h"] else "?", v["bw"])
    raison = incompatible_brut(texte, final)
    if raison:
        return None, raison
    return final, desc


def lire_media(texte, base):
    """(segments, durée cible, fin) d'une playlist media."""
    seq, cible, fin, dur, disc, segs = 0, 5.0, False, None, False, []
    for ligne in texte.splitlines():
        l = ligne.strip()
        try:
            if l.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                seq = int(l.split(":", 1)[1])
            elif l.startswith("#EXT-X-TARGETDURATION:"):
                cible = float(l.split(":", 1)[1])
            elif l.startswith("#EXTINF:"):
                dur = float(l[8:].split(",", 1)[0])
        except ValueError:
            pass
        if l.startswith("#EXT-X-DISCONTINUITY") and not l.startswith("#EXT-X-DISCONTINUITY-SEQUENCE"):
            disc = True
        elif l == "#EXT-X-ENDLIST":
            fin = True
        elif l and not l.startswith("#"):
            segs.append({"seq": seq + len(segs), "url": urllib.parse.urljoin(base, l),
                         "dur": dur or cible, "disc": disc})
            dur, disc = None, False
    return segs, cible, fin


def telecharger(url):
    """(octets, None) ou (None, raison): quelques essais, le proxy renvoie parfois 502/503."""
    erreur = "?"
    for essai in range(4):
        try:
            with http(url, 25) as r:
                data = r.read()
            if data:
                return data, None
            erreur = "réponse vide"
        except urllib.error.HTTPError as e:
            erreur = "HTTP %d" % e.code
        except Exception as e:
            erreur = type(e).__name__
        if essai < 3:
            time.sleep(0.5 * 2 ** essai)
    return None, erreur


def ts_valide(data):
    return len(data) >= 188 and data[0] == 0x47 and (len(data) < 377 or data[188] == 0x47)


def enregistrer_brut(media_url, sortie, duree, resume, ignores):
    """Écrit les segments bout à bout. Retourne le code de sortie, ou None si le premier segment
    n'est pas du TS (rien n'est écrit: on retombe alors sur ffmpeg)."""
    journal = None
    out = None

    def L(msg):
        if journal is not None:
            journal.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
            journal.flush()

    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))      # terminate() de tvstreamrecord
    debut = time.monotonic()
    dernier = None                  # numéro du dernier segment traité
    media = 0.0                     # secondes de média écrites
    ecrits = perdus = octets = 0
    sans_playlist = None            # début de la période sans playlist
    fin = False
    try:
        while not fin:
            if duree and (media >= duree or time.monotonic() - debut >= duree + 20):
                break
            try:
                final, texte = lire_playlist(media_url, 15)
                segs, cible, fin = lire_media(texte, media_url)
                sans_playlist = None
            except Exception as e:
                sans_playlist = sans_playlist or time.monotonic()
                L("playlist illisible (%s: %s)" % (type(e).__name__, e))
                if time.monotonic() - sans_playlist > 120:
                    L("plus de playlist depuis 2 min: abandon")
                    return 1
                time.sleep(1.5)
                continue
            if not segs:
                if fin:
                    break
                time.sleep(1.0)
                continue
            if dernier is None:
                nouveaux = segs if fin else segs[-3:]       # direct: comme ffmpeg, les 3 derniers
            else:
                if segs[-1]["seq"] < dernier - 20:
                    L("numérotation de la playlist revenue de %d à %d: nouvelle séquence"
                      % (dernier, segs[0]["seq"]))
                    dernier = segs[0]["seq"] - 1
                elif segs[0]["seq"] > dernier + 1:
                    manque = segs[0]["seq"] - dernier - 1
                    perdus += manque
                    L("%d segment(s) expiré(s) de la playlist avant téléchargement (%d..%d)"
                      % (manque, dernier + 1, segs[0]["seq"] - 1))
                nouveaux = [s for s in segs if s["seq"] > dernier]
            for s in nouveaux:
                if duree and media >= duree:
                    break
                data, erreur = telecharger(s["url"])
                dernier = s["seq"]
                if data is None or not ts_valide(data):
                    if out is None and ecrits == 0 and data is not None:
                        L("premier segment qui n'est pas du TS: repli sur ffmpeg")
                        return None
                    perdus += 1
                    L("segment %d PERDU (%s)" % (s["seq"], erreur or "pas du TS"))
                    continue
                if out is None:
                    out = open(sortie, "wb")
                    journal = open(sortie + ".log", "w")
                    journal.write(resume + "\n")
                    if ignores:
                        journal.write("arguments ignorés en mode brut: %s\n" % " ".join(ignores))
                    journal.write("%s début de l'enregistrement, playlist %s\n"
                                  % (time.strftime("%H:%M:%S"), media_url[:200]))
                    journal.flush()
                out.write(data)
                out.flush()
                ecrits += 1
                octets += len(data)
                media += s["dur"]
            if fin:
                break
            if not nouveaux:
                time.sleep(max(0.5, min(cible / 2.0, 2.0)))
    finally:
        if out is not None:
            L("fin: %d segments écrits (%.1f Mo, %.0f s de média), %d perdus"
              % (ecrits, octets / 1e6, media, perdus))
            out.close()
            journal.close()
    return 0 if ecrits else 1


media_url, raison = (None, "--ffmpeg") if forcer == "ffmpeg" else plan_brut(url, hmax, nosubs)
if dry and not media_url:
    print("mode ffmpeg (%s)" % raison)
if media_url:
    resume_brut = "mode brut (%s): segments TS copiés tels quels, aucun flux ne peut se perdre" % raison
    if dry:
        print(resume_brut)
        print("playlist: " + media_url)
        sys.exit(0)
    code = enregistrer_brut(media_url, sortie, duree, resume_brut, extra)
    if code is not None:
        sys.exit(code)
    raison = "le premier segment n'est pas du TS"
elif forcer == "brut":
    sys.exit("rec.py: mode brut impossible: " + raison)

try:
    p = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", url],
                       capture_output=True, text=True, timeout=90)
except subprocess.TimeoutExpired:
    sys.exit("rec.py: ffprobe a dépassé 90 s")
except OSError as e:
    sys.exit("rec.py: ffprobe introuvable (%s)" % e)
if p.returncode:
    sys.exit("rec.py: ffprobe a échoué: " + p.stderr.strip()[:300])
streams = json.loads(p.stdout).get("streams", [])


def br(s):
    """Débit d'un flux; en HLS, bit_rate est souvent absent -> variant_bitrate."""
    t = s.get("tags") or {}
    for v in (s.get("bit_rate"), t.get("variant_bitrate"), t.get("BPS")):
        try:
            if v and int(v):
                return int(v)
        except ValueError:
            pass
    return 0


vids = [s for s in streams if s.get("codec_type") == "video" and s.get("height")
        and (s.get("disposition") or {}).get("attached_pic") != 1]
if not vids:
    sys.exit("rec.py: aucune vidéo trouvée")
ok = [s for s in vids if s["height"] <= hmax] or [min(vids, key=lambda s: s["height"])]
video = max(ok, key=lambda s: (s["height"], br(s)))

groups = {}
for s in streams:
    if s.get("codec_type") != "audio":
        continue
    t = s.get("tags") or {}
    key = (t.get("language", ""), t.get("title", "") or t.get("handler_name", ""))
    if key not in groups or br(s) >= br(groups[key]):
        groups[key] = s
audios = sorted(groups.values(), key=lambda s: s["index"])

cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-i", url, "-map", "0:%d" % video["index"]]
for s in audios:
    cmd += ["-map", "0:%d" % s["index"]]
if not nosubs:
    cmd += ["-map", "0:s?"]      # sous-titres s'il y en a, sans erreur sinon
cmd += ["-c", "copy"] + extra + ["-y"]
if duree:
    # ffprobe a pris du temps: on le retranche pour finir à l'heure prévue
    cmd += ["-t", str(max(1, duree - int(time.time() - T0)))]
cmd += [sortie]

resume = "mode ffmpeg (%s). vidéo: %dx%d (flux %d); audio: %s; sous-titres: %s" % (
    raison,
    video.get("width", 0), video["height"], video["index"],
    ", ".join("flux %d %s" % (s["index"], (s.get("tags") or {}).get("language", "")) for s in audios) or "aucun",
    "aucun (--no-subs)" if nosubs else "0:s? (si présents)")

if dry:
    print(resume)
    print(" ".join(cmd))
    sys.exit(0)

log = open(sortie + ".log", "w")
log.write(resume + "\n" + " ".join(cmd) + "\n")
log.flush()
os.dup2(log.fileno(), 2)        # stderr de ffmpeg -> SORTIE.log (tvstreamrecord ne le voit pas comme une erreur)
os.execvp(cmd[0], cmd)          # on devient ffmpeg: terminate() de tvstreamrecord l'atteint directement
