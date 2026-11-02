/**
 * The identity-photo page's own slice of the kit. The phone link loads this instead of the
 * package root, so opening it does not pull in the rest of the portal.
 */
export { Alert, AlertDescription, AlertTitle } from './lib/components/ui/alert/index.js';
export { Button } from './lib/components/ui/button/index.js';
export { Card } from './lib/components/ui/card/index.js';
export {
	CameraCapture,
	CapturedFileCard,
	checkDocumentPhoto,
	isPdfFile,
	prepareDocumentCheck
} from './lib/components/ui/camera-capture/index.js';
export { Checkbox } from './lib/components/ui/checkbox/index.js';
export { cn } from './lib/utils';
