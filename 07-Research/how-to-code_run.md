# how to code_run

## Summary
Autonomous research into 'how to code_run' to fill a procedural_gap gap. 2 sources, 7 corroborated facts.

## Key Findings
- SEIRS Model Example Source: vignettes/SEIRS.Rmd SEIRS.Rmd config.yaml file The user should write a config.yaml file containing information pertaining to the data products used in the code run.  [sources: SEIRS Model Example •­ Simple Model Examples, SEINRD Model Example •­ Simple Model Examples]
- The example config.yaml file below describes a code run with inputs: disease/sars_cov2/SEIRS_model/parameters/static_params These inputs are listed in the register block, meaning that they should be downloaded to the local data store from an external source, with associated metadata stored in the local registry.  [sources: SEIRS Model Example •­ Simple Model Examples]
- These inputs are automatically converted into a read block by fair run (when data products are already present in the data registry, inputs should be listed in the read block).  [sources: SEIRS Model Example •­ Simple Model Examples, SEINRD Model Example •­ Simple Model Examples]
- A code run usually also has outputs, which are listed in the write block.  [sources: SEIRS Model Example •­ Simple Model Examples, SEINRD Model Example •­ Simple Model Examples]
- The data might now be processed in some way, or a model / analysis might bw carried out, after which the results should be saved in the local data store via one of the write_*() functions or link_write() .  [sources: SEIRS Model Example •­ Simple Model Examples, SEINRD Model Example •­ Simple Model Examples]
- When the code run is complete, finalise() should be called to register the all metadata with the local registry. fair pull Using the CLI tool, fair pull identifies any data products listed in the register field of the config.yaml .  [sources: SEIRS Model Example •­ Simple Model Examples, SEINRD Model Example •­ Simple Model Examples]
- In preparation for this, it translates the user-written config.yaml file for use by the Data Pipeline API.  [sources: SEIRS Model Example •­ Simple Model Examples, SEINRD Model Example •­ Simple Model Examples]

## Sources
- [SEIRS Model Example •­ Simple Model Examples](https://www.fairdatapipeline.org/rSimpleModel/articles/SEIRS.html) ([[learningMaterial/web/www-fairdatapipeline-org-rsimplemodel-articles-seirs-html-72273fc0.html|archived]])
- [SEINRD Model Example •­ Simple Model Examples](https://www.fairdatapipeline.org/rSimpleModel/articles/SEINRD.html) ([[learningMaterial/web/www-fairdatapipeline-org-rsimplemodel-articles-seinrd-html-1007785e.html|archived]])

## Follow-up Queries (gap fill)
- code_run definition means
- code_run example such as
- code_run code_run

<!-- research: 2 sources, 7 facts, 4 rounds -->