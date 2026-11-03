# Poly Haven assets

The real look (`?look=real`, `web/js/look.js` `ASSETS`) reads these files. Without them it
draws its own textures on a canvas and lights the scene with three.js' RoomEnvironment.

All are from Poly Haven and licensed CC0. Each file was downloaded from the URL that
`https://api.polyhaven.com/files/<id>` gives (on dl.polyhaven.org), and its size checked
against that listing, on 2026-11-03.

| Asset | Poly Haven | Kind | Resolution | Files used (bytes) | Licence |
|---|---|---|---|---|---|
| Kloofendal 48d Partly Cloudy (Pure Sky) | https://polyhaven.com/a/kloofendal_48d_partly_cloudy_puresky | HDRI (sky light) | 1k | `kloofendal_48d_partly_cloudy_puresky_1k.hdr` (1,435,119) | CC0 |
| Dirt Floor | https://polyhaven.com/a/dirt_floor | texture (the dirt road) | 1k JPG | `dirt_floor_diff_1k.jpg` (903,417), `dirt_floor_nor_gl_1k.jpg` (1,167,485), `dirt_floor_arm_1k.jpg` (652,716) | CC0 |
| Brushed Concrete | https://polyhaven.com/a/brushed_concrete | texture (steps, walls, ramps) | 1k JPG | `brushed_concrete_diff_1k.jpg` (847,278), `brushed_concrete_nor_gl_1k.jpg` (849,228), `brushed_concrete_arm_1k.jpg` (783,702) | CC0 |

Total of the files: 6,638,945 bytes.

- `diff`: colour; `nor_gl`: normal map (OpenGL convention, as three.js reads it); `arm`:
  ambient occlusion, roughness and metalness in red, green and blue (three.js' aoMap,
  roughnessMap and metalnessMap read those channels).
- The HDRI is 1k: its 2k file is 5,451,493 bytes, over the 5 MB allowed for one file.
- The side of each texture's square (2070 mm for Dirt Floor, 2500 mm for Brushed Concrete)
  is the asset's `dimensions` on `https://api.polyhaven.com/info/<id>`.
