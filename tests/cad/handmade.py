"""STEP text written by hand for the reader's tests (known answers)."""

HEAD = "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nFILE_NAME('t.stp','',(''),(''),'','','');\n" \
       "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n"
TAIL = "ENDSEC;\nEND-ISO-10303-21;\n"

MM_CONTEXT = """#900=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));
#901=(NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.));
#902=(GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNIT_ASSIGNED_CONTEXT((#900,#901)) REPRESENTATION_CONTEXT('',''));
"""
INCH_CONTEXT = """#900=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.));
#903=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#900);
#904=DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);
#905=(CONVERSION_BASED_UNIT('INCH',#903) LENGTH_UNIT() NAMED_UNIT(#904));
#901=(NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.));
#902=(GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNIT_ASSIGNED_CONTEXT((#905,#901)) REPRESENTATION_CONTEXT('',''));
"""
METRE_CONTEXT = """#900=(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.));
#901=(NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.));
#902=(GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNIT_ASSIGNED_CONTEXT((#900,#901)) REPRESENTATION_CONTEXT('',''));
"""


def box_brep(first: int, name: str, a: float, b: float, c: float) -> tuple[str, int]:
    """A faceted box [0,a]x[0,b]x[0,c] (POLY_LOOP faces); returns (text, id of the FACETED_BREP)."""
    P = [(0, 0, 0), (a, 0, 0), (a, b, 0), (0, b, 0), (0, 0, c), (a, 0, c), (a, b, c), (0, b, c)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (3, 7, 6, 2), (0, 4, 7, 3), (1, 2, 6, 5)]
    n = first
    lines = []
    pid = []
    for p in P:
        lines.append(f"#{n}=CARTESIAN_POINT('',({float(p[0])},{float(p[1])},{float(p[2])}));")
        pid.append(n)
        n += 1
    fids = []
    for f in faces:
        lines.append(f"#{n}=POLY_LOOP('',({','.join(f'#{pid[i]}' for i in f)}));")
        lines.append(f"#{n + 1}=FACE_OUTER_BOUND('',#{n},.T.);")
        lines.append(f"#{n + 2}=FACE('',(#{n + 1}));")
        fids.append(n + 2)
        n += 3
    lines.append(f"#{n}=CLOSED_SHELL('',({','.join(f'#{i}' for i in fids)}));")
    lines.append(f"#{n + 1}=FACETED_BREP('{name}',#{n});")
    return "\n".join(lines) + "\n", n + 1


def single(context: str, brep_text: str, brep_id: int) -> str:
    return HEAD + context + brep_text + \
        f"#990=CARTESIAN_POINT('',(0.,0.,0.));\n#991=DIRECTION('',(0.,0.,1.));\n#992=DIRECTION('',(1.,0.,0.));\n" \
        f"#993=AXIS2_PLACEMENT_3D('',#990,#991,#992);\n" \
        f"#994=FACETED_BREP_SHAPE_REPRESENTATION('',(#{brep_id},#993),#902);\n" + TAIL


def cube(context: str = MM_CONTEXT, size: float = 10.0) -> str:
    t, i = box_brep(1, "cube", size, size, size)
    return single(context, t, i)


# r = 10, from z = 0 to z = 20; the cylinder face is bounded by two circles (no seam edge)
CYLINDER = HEAD + MM_CONTEXT + """#1=CARTESIAN_POINT('',(0.,0.,0.));
#2=DIRECTION('',(0.,0.,1.));
#3=DIRECTION('',(1.,0.,0.));
#4=AXIS2_PLACEMENT_3D('',#1,#2,#3);
#5=CARTESIAN_POINT('',(0.,0.,20.));
#6=AXIS2_PLACEMENT_3D('',#5,#2,#3);
#7=CARTESIAN_POINT('',(10.,0.,0.));
#8=VERTEX_POINT('',#7);
#9=CARTESIAN_POINT('',(10.,0.,20.));
#10=VERTEX_POINT('',#9);
#11=CIRCLE('',#4,10.);
#12=CIRCLE('',#6,10.);
#13=EDGE_CURVE('',#8,#8,#11,.T.);
#14=EDGE_CURVE('',#10,#10,#12,.T.);
#15=CYLINDRICAL_SURFACE('',#4,10.);
#16=ORIENTED_EDGE('',*,*,#13,.T.);
#17=EDGE_LOOP('',(#16));
#18=FACE_OUTER_BOUND('',#17,.T.);
#19=ORIENTED_EDGE('',*,*,#14,.F.);
#20=EDGE_LOOP('',(#19));
#21=FACE_BOUND('',#20,.T.);
#22=ADVANCED_FACE('',(#18,#21),#15,.T.);
#23=PLANE('',#6);
#24=ORIENTED_EDGE('',*,*,#14,.T.);
#25=EDGE_LOOP('',(#24));
#26=FACE_OUTER_BOUND('',#25,.T.);
#27=ADVANCED_FACE('',(#26),#23,.T.);
#28=PLANE('',#4);
#29=ORIENTED_EDGE('',*,*,#13,.F.);
#30=EDGE_LOOP('',(#29));
#31=FACE_OUTER_BOUND('',#30,.T.);
#32=ADVANCED_FACE('',(#31),#28,.F.);
#33=CLOSED_SHELL('',(#22,#27,#32));
#34=MANIFOLD_SOLID_BREP('cyl',#33);
#35=ADVANCED_BREP_SHAPE_REPRESENTATION('',(#34,#4),#902);
""" + TAIL


def assembly() -> str:
    """Product 'block' (a 10 x 20 x 30 box at its origin) placed twice in product 'asm':
    once moved by (100, 0, 0); once turned 90 deg about z and moved by (0, 50, 0)."""
    box, bid = box_brep(1, "", 10.0, 20.0, 30.0)
    s = HEAD + MM_CONTEXT + box + """#100=APPLICATION_CONTEXT('automotive design');
#101=PRODUCT_CONTEXT('',#100,'mechanical');
#102=PRODUCT_DEFINITION_CONTEXT('part definition',#100,'design');
#110=PRODUCT('asm','asm','',(#101));
#111=PRODUCT_DEFINITION_FORMATION('','',#110);
#112=PRODUCT_DEFINITION('design','',#111,#102);
#113=PRODUCT_DEFINITION_SHAPE('','',#112);
#120=PRODUCT('block','block','',(#101));
#121=PRODUCT_DEFINITION_FORMATION('','',#120);
#122=PRODUCT_DEFINITION('design','',#121,#102);
#123=PRODUCT_DEFINITION_SHAPE('','',#122);
#130=CARTESIAN_POINT('',(0.,0.,0.));
#131=DIRECTION('',(0.,0.,1.));
#132=DIRECTION('',(1.,0.,0.));
#133=AXIS2_PLACEMENT_3D('',#130,#131,#132);
#134=CARTESIAN_POINT('',(100.,0.,0.));
#135=AXIS2_PLACEMENT_3D('',#134,#131,#132);
#136=CARTESIAN_POINT('',(0.,50.,0.));
#137=DIRECTION('',(0.,1.,0.));
#138=AXIS2_PLACEMENT_3D('',#136,#131,#137);
#140=SHAPE_REPRESENTATION('asm',(#133,#135,#138),#902);
#141=SHAPE_DEFINITION_REPRESENTATION(#113,#140);
#142=SHAPE_REPRESENTATION('block',(#133),#902);
#143=SHAPE_DEFINITION_REPRESENTATION(#123,#142);
#144=FACETED_BREP_SHAPE_REPRESENTATION('',(#BID,#133),#902);
#145=SHAPE_REPRESENTATION_RELATIONSHIP('','',#142,#144);
#150=NEXT_ASSEMBLY_USAGE_OCCURRENCE('1','block-1','',#112,#122,$);
#151=PRODUCT_DEFINITION_SHAPE('','',#150);
#152=ITEM_DEFINED_TRANSFORMATION('','',#133,#135);
#153=(REPRESENTATION_RELATIONSHIP('','',#142,#140) REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#152) SHAPE_REPRESENTATION_RELATIONSHIP());
#154=CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#153,#151);
#160=NEXT_ASSEMBLY_USAGE_OCCURRENCE('2','block-2','',#112,#122,$);
#161=PRODUCT_DEFINITION_SHAPE('','',#160);
#162=ITEM_DEFINED_TRANSFORMATION('','',#133,#138);
#163=(REPRESENTATION_RELATIONSHIP('','',#142,#140) REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#162) SHAPE_REPRESENTATION_RELATIONSHIP());
#164=CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#163,#161);
""".replace("#BID", f"#{bid}") + TAIL
    return s
