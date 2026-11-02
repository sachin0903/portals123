// A right-click menu.
//
// ONLY Root / Trigger / Content are new. Every other part of a menu — items, separators, group
// headings, submenus — is the SAME bits-ui component in both families (`bits-ui`'s context-menu
// exports re-export `menu/components/menu-item.svelte` and friends verbatim), so re-exporting the
// dropdown wrappers here is not a shortcut: it is what guarantees an item in a right-click menu and an
// item in a dropdown cannot drift apart in padding, focus ink or icon sizing. Re-exported rather than
// left to the caller so a consumer imports one namespace and never mixes the two by hand.
import Root from './context-menu.svelte';
import Trigger from './context-menu-trigger.svelte';
import Content from './context-menu-content.svelte';
import {
	Group,
	GroupHeading,
	Item,
	Label,
	Separator,
	Shortcut,
	Sub,
	SubContent,
	SubTrigger,
	CheckboxItem,
	RadioGroup,
	RadioItem
} from '../dropdown-menu/index.js';

export {
	CheckboxItem,
	Content,
	Group,
	GroupHeading,
	Item,
	Label,
	RadioGroup,
	RadioItem,
	Root,
	Separator,
	Shortcut,
	Sub,
	SubContent,
	SubTrigger,
	Trigger,
	Root as ContextMenu,
	Content as ContextMenuContent,
	Item as ContextMenuItem,
	Separator as ContextMenuSeparator,
	Trigger as ContextMenuTrigger
};
