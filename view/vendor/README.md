# Vendored code

`temml.min.js` is Temml 0.13.4, published at
<https://github.com/ronkok/Temml> under the MIT license, which
`temml-LICENSE.txt` reproduces. The file is the package's
`dist/temml.min.js` with one line appended:

```js
export default temml;
```

The published file is a browser script that assigns a global. The appended
line makes it a module that esbuild bundles like any other source file.
Nothing else in the file is changed.

Temml converts TeX to MathML. Chrome, Firefox, and Safari lay MathML out
natively, so the viewer renders mathematics without shipping a math font
and without a network fetch. `temml.min.d.ts` declares the one call the
bundle makes.

This is the only third-party code in the bundle. `view/README.md` states
why it is the exception to the rule that the bundle has no dependencies.
