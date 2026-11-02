import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	DETECT_EVERY_MS,
	FACE_MAX_WIDTH,
	FACE_MIN_WIDTH,
	heldLongEnough,
	holdStill,
	HOLD_MS,
	placeFace,
	STILL_TOLERANCE,
	watchFace,
	type FaceBox,
	type FacePlacement
} from './autocapture';
import type { FaceFinder } from './face-detector';

const PORTRAIT = 3 / 4;

/** A face as viewfinder fractions, by its centre and width. */
function face(width: number, centreX = 0.5, centreY = 0.5): FaceBox {
	return { x: centreX - width / 2, y: centreY - width / 2, width, height: width };
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('placeFace', () => {
	it('asks for a face, then for only one', () => {
		expect(placeFace([])).toBe('none');
		expect(placeFace([face(0.4, 0.3), face(0.4, 0.7)])).toBe('many');
	});

	it('takes one face in the middle of the oval at arm’s length', () => {
		expect(placeFace([face(0.4)])).toBe('placed');
		expect(placeFace([face(FACE_MIN_WIDTH)])).toBe('placed');
		expect(placeFace([face(FACE_MAX_WIDTH)])).toBe('placed');
		expect(placeFace([face(0.4, 0.59, 0.41)])).toBe('placed');
	});

	it('asks a face to move to the middle when it is off to a side, above or below', () => {
		expect(placeFace([face(0.4, 0.62)])).toBe('off-centre');
		expect(placeFace([face(0.4, 0.38)])).toBe('off-centre');
		expect(placeFace([face(0.4, 0.5, 0.62)])).toBe('off-centre');
		expect(placeFace([face(0.4, 0.5, 0.38)])).toBe('off-centre');
	});

	it('asks a face to move closer or back', () => {
		expect(placeFace([face(FACE_MIN_WIDTH - 0.01)])).toBe('far');
		expect(placeFace([face(FACE_MAX_WIDTH + 0.01)])).toBe('near');
	});

	it('asks a face too close to move back before it asks it to move to the middle', () => {
		expect(placeFace([face(0.8, 0.7)])).toBe('near');
	});

	it('asks a face far off to the side to move to the middle before it asks it to come closer', () => {
		expect(placeFace([face(0.2, 0.7)])).toBe('off-centre');
	});
});

describe('holdStill', () => {
	it('starts a hold when a face is placed and drops it when none is', () => {
		const hold = holdStill(null, face(0.4), 500);
		expect(hold).toEqual({ since: 500, face: face(0.4) });
		expect(holdStill(hold, null, 600)).toBeNull();
	});

	it('keeps the hold while the face stays within the tolerance of where it began', () => {
		const hold = holdStill(null, face(0.4), 0);
		const later = holdStill(hold, face(0.42, 0.5 + STILL_TOLERANCE / 2), 400);
		expect(later).toBe(hold);
	});

	it('starts again when the face moves, nearer or further', () => {
		const hold = holdStill(null, face(0.4), 0);
		expect(holdStill(hold, face(0.4, 0.5 + STILL_TOLERANCE + 0.01), 400)?.since).toBe(400);
		expect(holdStill(hold, face(0.4, 0.5, 0.5 - STILL_TOLERANCE - 0.01), 400)?.since).toBe(400);
		expect(holdStill(hold, face(0.4 + STILL_TOLERANCE + 0.01), 400)?.since).toBe(400);
	});

	it('starts again on a slow drift, measured from where the hold began', () => {
		const step = STILL_TOLERANCE * 0.75;
		const first = holdStill(null, face(0.4), 0);
		const second = holdStill(first, face(0.4, 0.5 + step), 100);
		expect(second).toBe(first);
		expect(holdStill(second, face(0.4, 0.5 + 2 * step), 200)?.since).toBe(200);
	});
});

describe('heldLongEnough', () => {
	it('is true once a hold has lasted HOLD_MS', () => {
		const hold = holdStill(null, face(0.4), 1000);
		expect(heldLongEnough(hold, 1000 + HOLD_MS - 1)).toBe(false);
		expect(heldLongEnough(hold, 1000 + HOLD_MS)).toBe(true);
		expect(heldLongEnough(null, 1_000_000)).toBe(false);
	});
});

describe('watchFace', () => {
	/** Animation frames that run only when told to, at the time given. */
	function frames(): { run: (now: number) => void; pending: () => number } {
		let queue: FrameRequestCallback[] = [];
		vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
			queue.push(callback);
			return queue.length;
		});
		vi.stubGlobal('cancelAnimationFrame', () => {
			queue = [];
		});
		return {
			run: (now) => {
				const due = queue;
				queue = [];
				for (const callback of due) callback(now);
			},
			pending: () => queue.length
		};
	}

	function playing(readyState = 4): HTMLVideoElement {
		return { readyState, videoWidth: 1280, videoHeight: 720 } as HTMLVideoElement;
	}

	async function loaded(): Promise<void> {
		for (let i = 0; i < 5; i++) await Promise.resolve();
	}

	function watch(
		find: FaceFinder | null,
		video = playing()
	): {
		placements: (FacePlacement | null)[];
		oncapture: ReturnType<typeof vi.fn>;
		stop: () => void;
	} {
		const placements: (FacePlacement | null)[] = [];
		const oncapture = vi.fn();
		const stop = watchFace(video, {
			aspect: PORTRAIT,
			onplacement: (placement) => placements.push(placement),
			oncapture,
			load: async () => find
		});
		return { placements, oncapture, stop };
	}

	it('takes the photo once, when a placed face has held still for HOLD_MS', async () => {
		const clock = frames();
		const find = vi.fn<FaceFinder>(() => [face(0.4)]);
		const { placements, oncapture } = watch(find);
		await loaded();

		for (let now = 0; now < HOLD_MS; now += DETECT_EVERY_MS) clock.run(now);
		expect(oncapture).not.toHaveBeenCalled();
		expect(placements.every((placement) => placement === 'placed')).toBe(true);

		clock.run(HOLD_MS);
		expect(oncapture).toHaveBeenCalledOnce();
		expect(clock.pending()).toBe(0);
	});

	it('takes no photo while the face keeps moving, then takes it once the face is still', async () => {
		const clock = frames();
		let centre = 0.45;
		const find = vi.fn<FaceFinder>(() => [face(0.4, centre)]);
		const { oncapture } = watch(find);
		await loaded();

		let now = 0;
		for (; now < 3 * HOLD_MS; now += DETECT_EVERY_MS) {
			centre = centre === 0.45 ? 0.5 : 0.45;
			clock.run(now);
		}
		expect(oncapture).not.toHaveBeenCalled();

		const stillFrom = now;
		for (; now <= stillFrom + HOLD_MS; now += DETECT_EVERY_MS) clock.run(now);
		expect(oncapture).toHaveBeenCalledOnce();
	});

	it('tells the person where the face is, and starts the hold again after it leaves', async () => {
		const clock = frames();
		const seen: FaceBox[][] = [[], [face(0.2)], [face(0.4)], [], [face(0.4)]];
		const find = vi.fn<FaceFinder>(() => seen.shift() ?? [face(0.4)]);
		const { placements, oncapture } = watch(find);
		await loaded();

		for (let now = 0; now <= 4 * DETECT_EVERY_MS; now += DETECT_EVERY_MS) clock.run(now);
		expect(placements).toEqual(['none', 'far', 'placed', 'none', 'placed']);

		// The hold began again at 400 ms, when the face came back.
		clock.run(400 + HOLD_MS - DETECT_EVERY_MS);
		expect(oncapture).not.toHaveBeenCalled();
		clock.run(400 + HOLD_MS);
		expect(oncapture).toHaveBeenCalledOnce();
	});

	it('looks at most every DETECT_EVERY_MS, and only once the video has a frame', async () => {
		const clock = frames();
		const video = playing(0);
		const find = vi.fn<FaceFinder>(() => []);
		watch(find, video);
		await loaded();

		clock.run(0);
		expect(find).not.toHaveBeenCalled();

		Object.assign(video, { readyState: 4 });
		for (let now = 16; now < 16 + DETECT_EVERY_MS; now += 16) clock.run(now);
		expect(find).toHaveBeenCalledOnce();
		expect(find).toHaveBeenCalledWith(video, PORTRAIT);
		clock.run(16 + DETECT_EVERY_MS);
		expect(find).toHaveBeenCalledTimes(2);
	});

	it('leaves the photo to the button when no face finder loads', async () => {
		const clock = frames();
		const { placements } = watch(null);
		await loaded();

		expect(placements).toEqual([null]);
		expect(clock.pending()).toBe(0);
	});

	it('stops and leaves the photo to the button when the face finder fails', async () => {
		const clock = frames();
		const find = vi.fn<FaceFinder>(() => {
			throw new Error('graph failed');
		});
		const { placements, oncapture } = watch(find);
		await loaded();

		clock.run(0);
		expect(placements).toEqual([null]);
		expect(oncapture).not.toHaveBeenCalled();
		expect(clock.pending()).toBe(0);
	});

	it('does nothing once stopped, even when the face finder loads after', async () => {
		const clock = frames();
		const find = vi.fn<FaceFinder>(() => [face(0.4)]);
		const { placements, stop } = watch(find);
		stop();
		await loaded();

		clock.run(0);
		expect(find).not.toHaveBeenCalled();
		expect(placements).toEqual([]);
		expect(clock.pending()).toBe(0);
	});

	it('stops looking when it is stopped mid-watch', async () => {
		const clock = frames();
		const find = vi.fn<FaceFinder>(() => [face(0.4)]);
		const { stop, oncapture } = watch(find);
		await loaded();
		clock.run(0);

		stop();
		clock.run(HOLD_MS);
		expect(find).toHaveBeenCalledOnce();
		expect(oncapture).not.toHaveBeenCalled();
	});
});
