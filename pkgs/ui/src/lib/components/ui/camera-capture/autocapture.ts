/**
 * Selfie auto-capture: the photo takes itself once one face sits in the oval and holds still.
 * The rules are pure functions here, so each is testable without a camera; `watchFace` runs them
 * against a live video. The "Take photo" button is always there as well: where no face finder
 * loads, or none is found, the person takes the photo themselves.
 */
import { loadFaceFinder, type FaceFinder } from './face-detector.js';

/** A face, as a box in fractions of the viewfinder: 0–1 on each axis, from its top left. */
export type FaceBox = { x: number; y: number; width: number; height: number };

/** Where the face is against the oval guide. `placed` is the one that counts down to a photo. */
export type FacePlacement = 'none' | 'many' | 'near' | 'off-centre' | 'far' | 'placed';

/** The narrowest a face may be, as a fraction of the viewfinder's width. Narrower is too far away
 *  for the face match. The oval is 62% of the width, and the box the face finder draws covers
 *  brow to chin and cheek to cheek, not the whole head. */
export const FACE_MIN_WIDTH = 0.28;
/** The widest a face may be before it is too close to fit the oval. */
export const FACE_MAX_WIDTH = 0.6;
/** How far the face's centre may be from the oval's centre, as a fraction of the viewfinder. */
export const FACE_CENTRE_TOLERANCE = 0.1;
/** How far a placed face may drift (centre or width, as a fraction of the viewfinder) and still
 *  count as still. Measured from where the hold began, so a slow drift also starts it again. */
export const STILL_TOLERANCE = 0.03;
/** How long a placed face holds still before the photo is taken. */
export const HOLD_MS = 1000;
/** The face finder looks at most this often. */
export const DETECT_EVERY_MS = 100;

/** What the person is told for each placement. */
export const PLACEMENT_HINT: Record<FacePlacement, string> = {
	none: 'Put your face in the oval',
	many: 'Only your face should be in the picture',
	near: 'Move back a little',
	'off-centre': 'Move your face to the middle of the oval',
	far: 'Move closer',
	placed: 'Hold still'
};

function centreX(face: FaceBox): number {
	return face.x + face.width / 2;
}

function centreY(face: FaceBox): number {
	return face.y + face.height / 2;
}

/**
 * Where the one face in the viewfinder is against the oval, which is centred. Too close is named
 * before off-centre: a face that fills
 * the frame is never centred, and backing away is what fixes both.
 */
export function placeFace(faces: readonly FaceBox[]): FacePlacement {
	const [face, ...others] = faces;
	if (!face) return 'none';
	if (others.length) return 'many';
	if (face.width > FACE_MAX_WIDTH) return 'near';
	if (
		Math.abs(centreX(face) - 0.5) > FACE_CENTRE_TOLERANCE ||
		Math.abs(centreY(face) - 0.5) > FACE_CENTRE_TOLERANCE
	) {
		return 'off-centre';
	}
	if (face.width < FACE_MIN_WIDTH) return 'far';
	return 'placed';
}

/** A placed face holding still: when the hold began, and where the face was then. */
export type Hold = { since: number; face: FaceBox };

/** How far ``face`` has moved from ``from``: the larger of the centre's shift and the change in
 *  width (moving nearer or further), as a fraction of the viewfinder. */
export function drift(from: FaceBox, face: FaceBox): number {
	return Math.max(
		Math.abs(centreX(face) - centreX(from)),
		Math.abs(centreY(face) - centreY(from)),
		Math.abs(face.width - from.width)
	);
}

/**
 * The hold after a look at time ``now`` that found ``face`` placed, or null where no face is
 * placed. A face that drifts further than ``STILL_TOLERANCE`` from where the hold began starts a
 * new hold from where it is now.
 */
export function holdStill(hold: Hold | null, face: FaceBox | null, now: number): Hold | null {
	if (!face) return null;
	if (!hold || drift(hold.face, face) > STILL_TOLERANCE) return { since: now, face };
	return hold;
}

/** Whether ``hold`` has lasted long enough at ``now`` for the photo to be taken. */
export function heldLongEnough(hold: Hold | null, now: number): boolean {
	return hold !== null && now - hold.since >= HOLD_MS;
}

/**
 * Watch a live selfie video for one face placed in the oval and held still, and call
 * ``oncapture`` once when it is. ``onplacement`` hears where the face is after every look, and
 * null when the face finder cannot be used (it did not load, or it failed): the watch then stops
 * and the person takes the photo with the button. Returns the function that stops the watch.
 */
export function watchFace(
	video: HTMLVideoElement,
	options: {
		aspect: number;
		onplacement: (placement: FacePlacement | null) => void;
		oncapture: () => void;
		load?: () => Promise<FaceFinder | null>;
	}
): () => void {
	let stopped = false;
	let frame = 0;
	let lastLook = -Infinity;
	let hold: Hold | null = null;

	function look(find: FaceFinder, now: number): void {
		lastLook = now;
		let faces: FaceBox[];
		try {
			faces = find(video, options.aspect);
		} catch {
			stopped = true;
			options.onplacement(null);
			return;
		}
		const placement = placeFace(faces);
		hold = holdStill(hold, placement === 'placed' ? (faces[0] ?? null) : null, now);
		options.onplacement(placement);
		if (heldLongEnough(hold, now)) {
			stopped = true;
			options.oncapture();
		}
	}

	void (options.load ?? loadFaceFinder)().then((find) => {
		if (stopped) return;
		if (!find) {
			options.onplacement(null);
			return;
		}
		const step = (now: number): void => {
			if (stopped) return;
			// HAVE_CURRENT_DATA: the video has a frame to look at.
			const ready = video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0;
			if (ready && now - lastLook >= DETECT_EVERY_MS) look(find, now);
			if (!stopped) frame = requestAnimationFrame(step);
		};
		frame = requestAnimationFrame(step);
	});

	return () => {
		stopped = true;
		cancelAnimationFrame(frame);
	};
}
