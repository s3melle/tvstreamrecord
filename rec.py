#!/usr/bin/env python3
"""Enregistre un flux (ffmpeg -c copy) en choisissant tout seul les pistes.

Deux façons de l'appeler :

  1. A la main
       rec.py URL SORTIE.ts [-t SECONDES] [-m HAUTEUR_MAX] [--dry]

  2. A la place de ffmpeg dans tvstreamrecord (page Configuration)
       Chemin ffmpeg      : /opt/tvstreamrecord/rec.py     (après chmod +x rec.py)
       Paramètres ffmpeg  : -m 720                         (vide = meilleure qualité)
     tvstreamrecord lance alors :  rec.py -i URL -y -t DUREE <paramètres> FICHIER
     Il faut donc VIDER les anciens paramètres (-map ..., -c copy, ...) : rec.py les gère.

Vidéo     : la plus haute qualité qui ne dépasse pas HAUTEUR_MAX (720 -> le 1280x720).
Audio     : une piste par langue/titre (la meilleure débit), donc toutes les langues,
            sans les doublons « même son à plusieurs débits ».
Sous-titres: ceux que le conteneur accepte en copie (en .ts : dvb_subtitle / dvb_teletext).
            Les sous-titres CC restent de toute façon dans la vidéo.

Une fois les pistes choisies, rec.py se remplace par ffmpeg (exec) : tvstreamrecord
arrête donc bien ffmpeg lui-même (terminate) et ffmpeg écrit dans SORTIE.log.
"""
import argparse, json, os, subprocess, sys, time

T0 = time.time()

ap = argparse.ArgumentParser(description="Enregistre un flux en choisissant les pistes")
ap.add_argument("-i", dest="entree", metavar="URL", help="URL (syntaxe ffmpeg, utilisée par tvstreamrecord)")
ap.add_argument("-y", action="store_true", help="accepté pour compatibilité ffmpeg (on écrase toujours)")
ap.add_argument("-loglevel", dest="loglevel", help="accepté pour compatibilité ffmpeg, ignoré")
ap.add_argument("-t", type=int, default=0, help="durée en secondes (0 = sans limite)")
ap.add_argument("-m", type=int, default=100000, help="hauteur vidéo max, ex 720")
ap.add_argument("--dry", action="store_true", help="afficher la commande sans la lancer")
ap.add_argument("chemins", nargs="+", metavar="[URL] SORTIE")
a = ap.parse_args()

if a.entree:
    url, sortie = a.entree, a.chemins[-1]
elif len(a.chemins) >= 2:
    url, sortie = a.chemins[0], a.chemins[1]
else:
    ap.error("il faut une URL et un fichier de sortie")

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

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
ok = [s for s in vids if s["height"] <= a.m] or [min(vids, key=lambda s: s["height"])]
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

# Sous-titres: en MPEG-TS seuls dvb_subtitle / dvb_teletext se copient (webvtt ferait échouer ffmpeg).
ts = sortie.lower().endswith((".ts", ".m2ts", ".mts"))
subs = [s for s in streams if s.get("codec_type") == "subtitle"
        and (not ts or s.get("codec_name") in ("dvb_subtitle", "dvb_teletext"))]
seen, subs_ok = set(), []
for s in subs:
    t = s.get("tags") or {}
    key = (t.get("language", ""), t.get("title", ""), s.get("codec_name"))
    if key not in seen:
        seen.add(key)
        subs_ok.append(s)

cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-i", url, "-map", "0:%d" % video["index"]]
for s in audios + subs_ok:
    cmd += ["-map", "0:%d" % s["index"]]
cmd += ["-c", "copy", "-y"]
if a.t:
    # ffprobe a pris du temps: on le retranche pour finir à l'heure prévue
    cmd += ["-t", str(max(1, a.t - int(time.time() - T0)))]
cmd += [sortie]

resume = "vidéo: %dx%d (flux %d); audio: %s; sous-titres: %s" % (
    video.get("width", 0), video["height"], video["index"],
    ", ".join("flux %d %s" % (s["index"], (s.get("tags") or {}).get("language", "")) for s in audios) or "aucun",
    ", ".join("flux %d %s" % (s["index"], (s.get("tags") or {}).get("language", "")) for s in subs_ok) or "aucun")

if a.dry:
    print(resume)
    print(" ".join(cmd))
    sys.exit(0)

log = open(sortie + ".log", "w")
log.write(resume + "\n" + " ".join(cmd) + "\n")
log.flush()
os.dup2(log.fileno(), 2)        # stderr de ffmpeg -> SORTIE.log (tvstreamrecord ne le voit pas comme une erreur)
os.execvp(cmd[0], cmd)          # on devient ffmpeg: terminate() de tvstreamrecord l'atteint directement
