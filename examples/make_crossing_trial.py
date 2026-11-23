"""Make a silent two-second film with brief crossings and movement elsewhere in the picture.

Pass the crossing directions in order, such as RLL, or an empty string for no crossings.
Show the film under a neutral name. Keep the directions and seed outside the reader's view.
"""
import argparse
from pathlib import Path
import random
import subprocess

import numpy as np


def pictures(directions, seed):
    rng = random.Random(seed)
    centres = [int((i + .5) * 120 / len(directions)) + rng.randint(-3, 3) for i in range(len(directions))]
    for frame in range(120):
        image = np.empty((180, 320, 3), dtype=np.uint8)
        image[:] = [34, 41, 52]
        image[:, 159:161] = [53, 61, 75]
        for row in range(3):
            x = (frame * (row + 1) * 4 + 53 * row) % 300
            y = 92 + 26 * row
            image[y:y + 9, x:x + 16] = [93, 160, 186]
        for centre, direction in zip(centres, directions):
            if abs(frame - centre) <= 4:
                x = 160 + (frame - centre) * 11 * (1 if direction == 'R' else -1)
                image[52:67, x - 7:x + 8] = [250, 106, 80]
        # The same colour also moves without reaching the line.
        if 35 <= frame < 46 or 76 <= frame < 92:
            x = 82 + frame % 10
            image[52:67, x - 7:x + 8] = [250, 106, 80]
        yield image


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('film', type=Path, help='output .mkv file')
    parser.add_argument('directions', help='crossings in time order, for example RLL')
    parser.add_argument('--seed', type=int, required=True, help='varies the times of the crossings')
    args = parser.parse_args()
    if args.film.exists():
        parser.error('choose a new output path')
    if len(args.directions) > 8 or any(d not in 'LR' for d in args.directions):
        parser.error("choose up to eight directions, each L or R")
    with subprocess.Popen(['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                           '-s', '320x180', '-r', '60', '-i', '-', '-c:v', 'ffv1', str(args.film)],
                          stdin=subprocess.PIPE) as encoder:
        with encoder.stdin as stream:
            for frame in pictures(args.directions, args.seed):
                stream.write(frame.tobytes())
        if encoder.wait():
            raise SystemExit('ffmpeg could not write the film')
