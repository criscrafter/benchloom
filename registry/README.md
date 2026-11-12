# Plugin registry

`index.json` lists instrument plugins that others wrote, so they can be
found and installed from Benchloom: **Plugins → Browse online** in the app,
or `benchloom plugins search` and `benchloom plugins install <id>` on the
command line.

Each entry says where the plugin's .zip is and its SHA-256 checksum.
Benchloom installs a plugin only when the downloaded file has exactly that
checksum, so a listed version cannot change after it was added. Plugins are
Python code written by their authors: being listed means the entry was
checked, not that the code is safe. Install plugins from people you trust.

## Adding your plugin

1. Put the plugin folder (with its `plugin.toml`) into a .zip and publish it
   at a permanent https:// address, for example as a GitHub release asset.
   A new version gets a new file; never replace a listed one.
2. Make the entry:

   ```
   benchloom plugins entry my-plugin-1.0.0.zip --url https://github.com/you/my-plugin/releases/download/v1.0.0/my-plugin-1.0.0.zip
   ```

   It prints the entry with the checksum and the details from `plugin.toml`.
   Fill in `author`, `license` and `homepage`.
3. Open a pull request that adds the entry to `plugins` in `index.json`
   (or replaces the entry of the older version). Say which instruments you
   tried it with.

The plugin keeps its own licence; the entry only points to it. How to write
a plugin is in the main README, under "A plugin".

## Your own list

A company can keep its own list in the same format: set `plugin_index` in
`~/.benchloom/config.json` to its https:// address or to a file path.
