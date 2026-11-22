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

Surveillance pendant les 3 premières minutes (REC_FENETRE, en secondes) : ffmpeg fige ses flux
au démarrage et jette ceux qui apparaissent ensuite (ex. Pluto: nouveau flux vidéo après un
changement de clip -> plus que le son). rec.py regarde donc ffmpeg ET le flux source (ffprobe
toutes les 20 s, REC_SONDE) ; si le format change (flux apparu, PID, codec ou résolution
différents), il arrête ffmpeg, SUPPRIME l'enregistrement commencé et relance depuis zéro avec
le nouveau format (l'heure de fin reste celle prévue, 5 relances au plus). Si rien ne change
pendant ces 3 minutes, ou après elles, l'enregistrement est conservé et plus rien n'est surveillé.
tvstreamrecord arrête rec.py (SIGTERM) : ffmpeg est arrêté proprement. Journal: SORTIE.log.
"""
import json, os, re, signal, subprocess, sys, time

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
FENETRE = int(os.environ.get("REC_FENETRE", "180"))
SONDE = int(os.environ.get("REC_SONDE", "20"))
MAX_RELANCES = int(os.environ.get("REC_RELANCES", "5"))


def lire_flux():
    """Liste des flux de la source (ffprobe), ou RuntimeError."""
    try:
        p = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", url],
                           capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffprobe a dépassé 90 s")
    except OSError as e:
        raise RuntimeError("ffprobe introuvable (%s)" % e)
    if p.returncode:
        raise RuntimeError("ffprobe a échoué: " + p.stderr.strip()[:300])
    return json.loads(p.stdout).get("streams", [])


def signature(streams):
    """Empreinte du format: type, codec, taille et identifiant (PID) de chaque flux."""
    return sorted((str(s.get("codec_type")), str(s.get("codec_name")), s.get("width") or 0,
                   s.get("height") or 0, str(s.get("id"))) for s in streams
                  if s.get("codec_type") in ("video", "audio", "subtitle"))


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


def preparer(streams):
    """Choix des flux. Renvoie (commande, résumé) ou lève RuntimeError."""
    vids = [s for s in streams if s.get("codec_type") == "video" and s.get("height")
            and (s.get("disposition") or {}).get("attached_pic") != 1]
    if not vids:
        raise RuntimeError("aucune vidéo trouvée")
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

    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "warning", "-i", url,
           "-map", "0:%d" % video["index"]]
    for s in audios:
        cmd += ["-map", "0:%d" % s["index"]]
    if not nosubs:
        cmd += ["-map", "0:s?"]      # sous-titres s'il y en a, sans erreur sinon
    cmd += ["-c", "copy"] + extra + ["-y"]
    if duree:
        # le temps déjà écoulé (analyse, relances) est retranché pour finir à l'heure prévue
        cmd += ["-t", str(max(1, int(duree - (time.time() - T0))))]
    cmd += [sortie]
    resume = "vidéo: %dx%d (flux %d); audio: %s; sous-titres: %s" % (
        video.get("width", 0), video["height"], video["index"],
        ", ".join("flux %d %s" % (s["index"], (s.get("tags") or {}).get("language", "")) for s in audios) or "aucun",
        "aucun (--no-subs)" if nosubs else "0:s? (si présents)")
    return cmd, resume


try:
    streams = lire_flux()
    cmd, resume = preparer(streams)
except RuntimeError as e:
    sys.exit("rec.py: " + str(e))

if dry:
    print("mode ffmpeg. " + resume)
    print(" ".join(cmd))
    sys.exit(0)

log = open(sortie + ".log", "w")


def note(msg):
    log.write("[%s] rec.py: %s\n" % (time.strftime("%H:%M:%S"), msg))
    log.flush()


arret = [False]
proc = [None]


def stop(*a):
    arret[0] = True
    p = proc[0]
    if p and p.poll() is None:
        p.terminate()


signal.signal(signal.SIGTERM, stop)      # terminate() de tvstreamrecord
signal.signal(signal.SIGINT, stop)


def arreter(p):
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(8)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


lecteur = open(sortie + ".log", "r")
relances = 0
while True:
    note("mode ffmpeg. " + resume)
    note(" ".join(cmd))
    sig = signature(streams)
    lecteur.seek(0, 2)
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
    proc[0] = p
    prochaine = time.time() + SONDE
    nouveau = None
    while p.poll() is None and not arret[0]:
        time.sleep(1)
        now = time.time()
        if now - T0 > FENETRE:
            break                                    # 3 minutes passées: on oublie
        if duree and now - T0 > duree - 10:
            break
        motif = None
        m = re.search(r"New (video|audio|subtitle) stream", lecteur.read())
        if m:
            motif = "ffmpeg signale un flux %s apparu après le démarrage" % m.group(1)
        elif now >= prochaine:
            prochaine = now + SONDE
            try:
                s2 = lire_flux()
                if signature(s2) != sig:
                    motif = "le format de la source a changé"
            except RuntimeError:
                pass
        if motif:
            if relances >= MAX_RELANCES:
                note("%s, mais %d relances déjà faites: on garde" % (motif, relances))
                break
            try:
                s2 = lire_flux()
                cmd2, resume2 = preparer(s2)
            except RuntimeError as e:
                note("%s, mais analyse impossible (%s): on continue tel quel" % (motif, e))
                continue
            nouveau = (motif, s2, cmd2, resume2)
            break
    if nouveau and not arret[0]:
        motif, streams, cmd, resume = nouveau
        arreter(p)
        try:
            os.remove(sortie)
        except OSError:
            pass
        relances += 1
        # la durée restante est celle de l'heure prévue: on recalcule -t
        cmd, resume = preparer(streams)
        note("%s -> enregistrement précédent supprimé, relance %d" % (motif, relances))
        continue
    break

while p.poll() is None and not arret[0]:     # plus de surveillance: ffmpeg finit seul (-t ou SIGTERM)
    time.sleep(1)
if arret[0]:
    arreter(p)
note("terminé (code %s)" % p.returncode)
sys.exit(0 if os.path.exists(sortie) and os.path.getsize(sortie) else (p.returncode or 1))
