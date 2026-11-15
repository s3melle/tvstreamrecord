#!/usr/bin/env python3
"""Enregistre un flux en choisissant tout seul les pistes.

  rec.py URL SORTIE.ts [-t SECONDES] [-m HAUTEUR_MAX] [--dry]

Vidéo: la plus haute qualité qui ne dépasse pas HAUTEUR_MAX (720 -> le 1280x720; sans -m: la meilleure).
Audio: une piste par langue/titre (la meilleure), donc toutes les langues, sans les doublons
       « un même son à plusieurs débits ». Les sous-titres CC restent dans la vidéo.
"""
import argparse, json, subprocess, sys

ap = argparse.ArgumentParser()
ap.add_argument("url")
ap.add_argument("sortie")
ap.add_argument("-t", type=int, default=0, help="durée en secondes (0 = sans limite)")
ap.add_argument("-m", type=int, default=100000, help="hauteur vidéo max, ex 720")
ap.add_argument("--dry", action="store_true", help="afficher la commande sans la lancer")
a = ap.parse_args()

p = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", a.url],
                   capture_output=True, text=True, timeout=90)
if p.returncode:
    sys.exit("ffprobe a échoué: " + p.stderr.strip()[:300])
streams = json.loads(p.stdout).get("streams", [])

def br(s):
    try:
        return int(s.get("bit_rate") or 0)
    except ValueError:
        return 0

vids = [s for s in streams if s.get("codec_type") == "video" and s.get("height")]
if not vids:
    sys.exit("aucune vidéo trouvée")
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

cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", a.url, "-map", "0:%d" % video["index"]]
for s in audios:
    cmd += ["-map", "0:%d" % s["index"]]
cmd += ["-c", "copy", "-y"] + (["-t", str(a.t)] if a.t else []) + [a.sortie]

print("vidéo: %dx%d (flux %d); audio: %s" % (video.get("width", 0), video["height"], video["index"],
      ", ".join("flux %d %s" % (s["index"], (s.get("tags") or {}).get("language", "")) for s in audios) or "aucun"))
print(" ".join(cmd))
if not a.dry:
    with open(a.sortie + ".log", "w") as log:
        sys.exit(subprocess.call(cmd, stderr=log))
