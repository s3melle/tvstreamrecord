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

Options de rec.py : -m HAUTEUR_MAX, --no-subs (ne pas mapper les sous-titres), --dry.
Tout autre argument (ex. -metadata title=...) est transmis tel quel à ffmpeg, avant le
fichier de sortie. Les anciens -map ... , -c ... et -loglevel ... sont ignorés : rec.py gère.

Vidéo      : la plus haute qualité qui ne dépasse pas HAUTEUR_MAX (720 -> le 1280x720).
Audio      : une piste par langue/titre (la meilleure débit), donc toutes les langues,
             sans les doublons « même son à plusieurs débits ».
Sous-titres: -map 0:s? (tous ceux qui existent, aucune erreur s'il n'y en a pas).

Une fois les pistes choisies, rec.py se remplace par ffmpeg (exec) : tvstreamrecord
arrête donc bien ffmpeg lui-même (terminate) et ffmpeg écrit dans SORTIE.log.
"""
import json, os, subprocess, sys, time

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

resume = "vidéo: %dx%d (flux %d); audio: %s; sous-titres: %s" % (
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
