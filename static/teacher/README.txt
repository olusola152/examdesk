PUT YOUR 3D TEACHER HERE
========================

A) Quick: the model only (he stands, sways and nods while he talks)
   1. On Sketchfab, open the model and click "Download 3D Model".
   2. Choose "glb" (or "glTF"). You must be signed in to Sketchfab.
   3. Rename the .glb file to:   teacher.glb   and put it in this folder.
      (If you chose glTF: extract the whole zip into this folder - scene.gltf, scene.bin and the textures folder.)
   4. Refresh the lecture page. The cartoon teacher is replaced automatically.

B) Best: make him move his arms and talk with gestures (free, mixamo.com)
   1. On Sketchfab download the "FBX" (or OBJ) version of the model.
   2. Go to mixamo.com -> Upload Character -> choose that file -> place the markers (chin, wrists, elbows, knees, groin) -> Next.
   3. Search the animation "Talking" -> Download -> Format FBX, Skin: WITH SKIN -> save it in this folder as   teacher.fbx
   4. Search "Idle" (standing) -> Download -> Skin: WITHOUT SKIN -> save as   idle.fbx
   5. Optional: search "Pointing" or "Writing" -> WITHOUT SKIN -> save as   writing.fbx   (used while he writes on the board)
   If you have teacher.glb AND teacher.fbx, teacher.glb is used - delete one.

C) Credit the creator
   Most Sketchfab models are "CC Attribution": you must credit the author. Check the licence on the model page,
   then add this line to your .env file (shown small under the teacher):
       TEACHER_CREDIT=3D model "Confident Professional in a Three-Piece Suit" by restore50 (Sketchfab, CC BY)

Tips
 - Big models (200k+ triangles, 30+ MB) load slowly for students on mobile data. The browser keeps it cached after the first time.
 - No model in this folder = the built-in cartoon teacher is used.
