import "./story.css";
import { Story } from "@/components/story/Story";
import { buildStory } from "@/lib/story";
import { readStoryInputs } from "@/lib/story.server";

/**
 * The story page. Rendered on the server at build time: the prompt files and eval sets are read
 * from the repo here, and only the derived numbers and strings reach the browser.
 */
export default function StoryPage() {
  return <Story data={buildStory(readStoryInputs())} />;
}
