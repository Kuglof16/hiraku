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
| Kloofendal 48d Partly Cloudy | https://polyhaven.com/a/kloofendal_48d_partly_cloudy | HDRI (the sky seen behind the cut views, with its horizon) | 1k | `kloofendal_48d_partly_cloudy_1k.hdr` (1,637,206) — URL https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/1k/kloofendal_48d_partly_cloudy_1k.hdr | CC0 |
| Gravel Road | https://polyhaven.com/a/gravel_road | texture (the gravel road under the vehicle) | 1k JPG | `gravel_road_diff_1k.jpg` (684,038), `gravel_road_nor_gl_1k.jpg` (983,723), `gravel_road_arm_1k.jpg` (358,309) — URLs https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/gravel_road/gravel_road_<map>_1k.jpg | CC0 |

Total of the files: 10,302,221 bytes (6,638,945 before the two added on 2026-11-03 for the cut views: Kloofendal 48d Partly Cloudy and Gravel Road, 3,663,276).

- `diff`: colour; `nor_gl`: normal map (OpenGL convention, as three.js reads it); `arm`:
  ambient occlusion, roughness and metalness in red, green and blue (three.js' aoMap,
  roughnessMap and metalnessMap read those channels).
- The HDRIs are 1k: their 2k files are 5,451,493 bytes (Pure Sky) and 6,480,797 bytes (Partly Cloudy), over the 5 MB allowed for one file.
- The side of each texture's square (2070 mm for Dirt Floor, 2500 mm for Brushed Concrete, 2000 mm for Gravel Road)
  is the asset's `dimensions` on `https://api.polyhaven.com/info/<id>`.
