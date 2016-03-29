import scipy.ndimage
import progressbar
import numpy as np
import collections
import pyfftw
import pprint
import math
import mido
import wave
import sys

pyfftw.interfaces.cache.enable()

class WavFFT(object):
    def __init__(self, filename, chunksize):
        with wave.open(filename, "r") as wavfile:
            self.sampwidth = wavfile.getsampwidth()
            self.framerate = wavfile.getframerate()
            self.chunksize = chunksize
            self.offset = int(2**(8*max(self.sampwidth, 4))/2) #max 32-bit
            self.size = np.int32
            self.fft = None
            self.maxfreq = None
            dither = False
            if self.sampwidth == 1:
                self.size=np.int8
            elif self.sampwidth == 2:
                self.size=np.int16
            self.wav = np.zeros(wavfile.getnframes(), dtype=self.size)
            if self.sampwidth > 4:
                for i in range(0,wavfile.getnframes()):
                    self.wav[i] = int.from_bytes(wavfile.readframes(1)[:self.sampwidth], byteorder="little", signed=True) / 2**32 # 64-bit int to 32-bit
            else:
                for i in range(0,wavfile.getnframes()):
                    self.wav[i] = int.from_bytes(wavfile.readframes(1)[:self.sampwidth], byteorder="little", signed=True)
            self.wav -= int(np.average(self.wav))

    def get_fft(self, pbar=True):
        if not self.fft:
            spacing = float(self.framerate) / self.chunksize
            avgdata = np.zeros(self.chunksize // 2, dtype=np.float64)
            c = None
            offset = None
            bar = progressbar.ProgressBar(widgets=[progressbar.Percentage(), " ", progressbar.Bar()])
            for i in range(0,len(self.wav),self.chunksize):
                data = np.array(self.wav[i:i+self.chunksize], dtype=self.size)
                if len(data) != self.chunksize:
                    continue
                fft = pyfftw.interfaces.numpy_fft.fft(data)
                fft = np.abs(fft[:self.chunksize/2])
                avgdata += fft
                del data
                del fft
            if max(avgdata) == 0:
                self.chunksize = self.chunksize // 2
                self.fft = self.get_fft(pbar=pbar)
            else:
                self.fft = (avgdata, spacing)
        return self.fft

    def plot_waveform(self):
        import matplotlib.pyplot as plt
        plot = plt.figure(1)
        plt.plot(range(len(self.wav)), self.wav, "r")
        plt.xlabel("Time")
        plt.ylabel("Amplitude")
        plt.title("Audio File Waveform")
        plt.show(1)

    def plot_fft(self):
        import matplotlib.pyplot as plt
        plot = plt.figure(1)
        plt.plot([(i*self.fft[1])+self.fft[1] for i in range(len(self.fft[0][1:1000//self.fft[1]]))], list(fft[0][1:1000//self.fft[1]]), "r")
        plt.xlabel("Frequency (Hz)")
        plt.ylabel("Intensity (abs(fft[freq]))")
        plt.title("FFT Analysis")
        plt.show(1)

    def get_max_freq(self):
        if not self.maxfreq:
            fft = self.get_fft()
            self.maxfreq = (np.argmax(fft[0][1:]) * fft[1]) + (fft[1] / 2)
        return self.maxfreq

class MIDIParser(object):
    def __init__(self, path, wav):
        results = collections.defaultdict(lambda: [])
        notes = collections.defaultdict(lambda: [])
        self.notecount = 0
        self.maxnotes = 0
        with mido.MidiFile(path, "r") as mid:
            time = 0
            for message in mid:
                time += message.time
                if "channel" in message.__dict__ and message.channel == 10: continue  # channel 10 is reserved for percussion
                if message.type == "note_on":
                    notes[message.note].append(time)
                    self.maxnotes = max(sum(len(i) for i in notes.values()), self.maxnotes)
                elif message.type == "note_off":
                    results[int(round(notes[message.note][0]*sample.framerate))].append((int((time - notes[message.note][0]) * wav.framerate), wav.get_max_freq() / self.note_to_freq(message.note), 1 if message.velocity / 127 == 0 else message.velocity / 127))
                    notes[message.note].pop(0)
                    self.notecount += 1
            for ntime, nlist in notes.items():
                for note in nlist:
                    results[int(round(notes[note][0]*sample.framerate))].append((int((ntime - time) * wav.framerate), wav.get_max_freq() / self.note_to_freq(note), 64))
                    self.notecount += 1
            self.notes = sorted(results.items())
            self.length = self.notes[-1][0] + max(self.notes[-1][1])[0]
    
    def note_to_freq(self, notenum):
        # https://en.wikipedia.org/wiki/MIDI_Tuning_Standard
        return (2.0**((notenum-69)/12.0)) * 440.0

notecache = {}

def render_note(note, sample, threshold):
    scaled = scipy.ndimage.zoom(sample.wav[:max(int((note[0] + threshold)*note[1]),len(sample.wav))], note[1]) * note[2]
    if len(scaled) < note[0] + threshold:
        return scaled
    else:
        scaled = scaled[:note[0] + threshold]
        cutoff = np.argmin([abs(i)+(d*20) for d, i in enumerate(scaled[note[0]:])])
        return scaled[:note[0]+cutoff]

def hash_array(arr):
    arr.flags.writeable = False
    result = hash(arr.data)
    arr.flags.writeable = True
    return result

print("Loading sample into memory...")
sample = WavFFT(sys.argv[1] if len(sys.argv) > 1 else "doot.wav", 8192)
threshold = int(float(sample.framerate) * 0.075)
print("Analyzing sample...")
ffreq = sample.get_max_freq()
print("Fundamental Frequency: {} Hz".format(ffreq))
print("Parsing MIDI...")
midi = MIDIParser(sys.argv[2] if len(sys.argv) > 2 else "tetris.mid", sample)
print("Rendering audio...")
output = np.zeros(midi.length + 1 + threshold, dtype=np.float64)
bar = progressbar.ProgressBar(widgets=[progressbar.Percentage(), " ", progressbar.Bar(), " ", progressbar.ETA()], max_value=midi.notecount)
c = 0
tick = 10
for time, notes in midi.notes:
    for note in notes:
        if note[:2] in notecache:
            sbl = len(notecache[note[:2]][2])
            output[time:time+sbl] += notecache[note[:2]][2]
            notecache[note[:2]] = (notecache[note[:2]][0] + 1, notecache[note[:2]][1], notecache[note[:2]][2])
        else:
            rendered = render_note(note, sample, threshold)
            sbl = len(rendered)
            output[time:min(time+sbl, len(output))] += rendered[:min(time+sbl, len(output))-time]
            notecache[note[:2]] = (1, time, rendered)
        c += 1
        bar.update(c)
    tick -= 1
    if tick == 0:
        tick = 10
        for k in list(notecache.keys()):
            if (time - notecache[k][1]) > (7.5*sample.framerate) and notecache[k][0] <= 2:
                del notecache[k]

output *= ((2**32) / (abs(output.max()) + (abs(output.min()))))
output -= output.min() + (2**32/2)
    
with wave.open(sys.argv[3] if len(sys.argv) > 3 else "out.wav", "w") as outwav:
    outwav.setframerate(sample.framerate)
    outwav.setnchannels(1)
    outwav.setsampwidth(4)
    outwav.setnframes(len(output))
    outwav.writeframesraw(output.astype(np.int32))
