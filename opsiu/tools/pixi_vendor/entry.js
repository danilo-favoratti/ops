// Install static shader/uniform synchronization before renderer initialization.
// This compatibility module removes PixiJS's requirement for CSP unsafe-eval.
import "pixi.js/unsafe-eval";

export {
  Application,
  Circle,
  Container,
  Graphics,
  Rectangle,
  Text,
  VERSION,
} from "pixi.js";
